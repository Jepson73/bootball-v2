#!/usr/bin/env python3
# ruff: noqa
import warnings
warnings.filterwarnings("ignore", message="X does not have valid feature names")
"""
Walk-forward backtest V4 — Wave 1 features, expanded historical validation pool.

Key improvements over V3:
  1. Wave 1 features (20 new) via features_v1.FeatureBuilder:
       rolling form (shots/possession/corners/pass-acc/yellow), trailing
       league context, H2H history.
  2. Expanded validation: historical_odds.db (fdco 2019-2023) + production
       fixture_odds (2025-2026) → ~20,000 fixtures vs V3's 2,334.
  3. Three non-overlapping test windows (2022 / 2023 / 2025-26) for
       the Phase 2 success bar check.
  4. Features pre-computed ONCE per N value for all fixtures, then
       sliced per window — avoids rebuilding for each of the 6 runs.
  5. O(log n) cumulative-prefix-sum standings lookup (vs V3's O(n_matches)
       per fixture).

Output: scripts/analysis/backtest_results_v4.json
"""

import json
import logging
import sqlite3
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
from scipy.optimize import brentq
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_v1 import FeatureBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backtest_v4")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH       = PROJECT_ROOT / "data" / "football.db"
HIST_DB_PATH  = Path(__file__).resolve().parent / "historical_odds.db"
OUTPUT_PATH   = Path(__file__).resolve().parent / "backtest_results_v4.json"

BOT_MIN_EV       = 0.05
MODEL_WEIGHT     = 0.35
KELLY_FRACTION   = 0.25
CALIB_HOLDOUT_FRAC = 0.30
N_BOOTSTRAP      = 2000
DEFAULT_GF       = 1.0
DEFAULT_GA       = 1.0
MAX_TRAIN_SAMPLES = 40000   # cap per window; keeps model fitting fast; 40K >> feature dimensionality

WINDOWS = [
    {"name": "2022",    "test_start": "2022-01-01", "test_end": "2023-01-01"},
    {"name": "2023",    "test_start": "2023-01-01", "test_end": "2025-01-01"},
    {"name": "2025-26", "test_start": "2025-01-01", "test_end": "2027-01-01"},
]

ROLLING_NS = [5, 10]
MARKET_SLOTS = {"h2h": 3, "btts": 2, "ou25": 2, "ou15": 2}


# ── Shin de-vigging ───────────────────────────────────────────────────────────

def shin_probabilities(odds: list) -> list:
    raw = np.array([1.0 / o for o in odds])
    W   = raw.sum()
    if abs(W - 1.0) < 1e-6:
        return raw.tolist()
    def objective(z):
        if z >= 1.0:
            return 1e10
        p = (np.sqrt(z**2 + 4 * (1 - z) * raw / W) - z) / (2 * (1 - z))
        return p.sum() - 1.0
    try:
        z_star = brentq(objective, 0.0, 0.999, maxiter=200)
        p = (np.sqrt(z_star**2 + 4 * (1 - z_star) * raw / W) - z_star) / (2 * (1 - z_star))
        p /= p.sum()
        return p.tolist()
    except Exception:
        return (raw / W).tolist()


def market_blend(p_model: float, all_odds: list, outcome_idx: int) -> Tuple[float, Optional[float]]:
    if not all_odds or any(o is None or o < 1.01 for o in all_odds):
        return p_model, None
    try:
        devigged  = shin_probabilities(all_odds)
        p_market  = devigged[outcome_idx]
        p_blended = MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * p_market
        return p_blended, p_market
    except Exception:
        return p_model, None


# ── Data loading ──────────────────────────────────────────────────────────────

def load_all_training_fixtures(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute("""
        SELECT id, league_id, season, home_team_id, away_team_id,
               date, goals_home, goals_away, outcome
        FROM fixtures
        WHERE status = 'FT'
          AND goals_home IS NOT NULL AND goals_away IS NOT NULL
          AND outcome IS NOT NULL
          AND season >= 2019
        ORDER BY date ASC
    """).fetchall()
    cols = ["id","league_id","season","home_team_id","away_team_id",
            "date","goals_home","goals_away","outcome"]
    logger.info(f"Loaded {len(rows):,} training fixtures (2019+)")
    return [dict(zip(cols, r)) for r in rows]


def load_validation_fixtures(conn: sqlite3.Connection) -> List[dict]:
    prod_rows = conn.execute("""
        SELECT
            f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
            f.date, f.goals_home, f.goals_away, f.outcome,
            MAX(CASE WHEN fo.bet_type='h2h'        THEN fo.odd_home     END),
            MAX(CASE WHEN fo.bet_type='h2h'        THEN fo.odd_draw     END),
            MAX(CASE WHEN fo.bet_type='h2h'        THEN fo.odd_away     END),
            MAX(CASE WHEN fo.bet_type='btts'       THEN fo.odd_btts_yes END),
            MAX(CASE WHEN fo.bet_type='btts'       THEN fo.odd_btts_no  END),
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over     END),
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under    END),
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over15   END),
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under15  END),
            'production'
        FROM fixtures f
        JOIN fixture_odds fo ON fo.fixture_id = f.id
        WHERE f.status = 'FT'
          AND f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
          AND f.outcome IS NOT NULL AND f.season >= 2025
        GROUP BY f.id ORDER BY f.date ASC
    """).fetchall()

    hist_rows = conn.execute("""
        SELECT
            f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
            f.date, f.goals_home, f.goals_away, f.outcome,
            MAX(CASE WHEN hfo.bet_type='h2h'        THEN hfo.odd_home  END),
            MAX(CASE WHEN hfo.bet_type='h2h'        THEN hfo.odd_draw  END),
            MAX(CASE WHEN hfo.bet_type='h2h'        THEN hfo.odd_away  END),
            NULL, NULL,
            MAX(CASE WHEN hfo.bet_type='over_under' THEN hfo.odd_over  END),
            MAX(CASE WHEN hfo.bet_type='over_under' THEN hfo.odd_under END),
            NULL, NULL,
            'fdco'
        FROM fixtures f
        JOIN hist.fixture_odds hfo ON hfo.fixture_id = f.id
        WHERE f.status = 'FT'
          AND f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
          AND f.outcome IS NOT NULL AND f.season BETWEEN 2019 AND 2024
        GROUP BY f.id ORDER BY f.date ASC
    """).fetchall()

    cols = ["id","league_id","season","home_team_id","away_team_id",
            "date","goals_home","goals_away","outcome",
            "odd_home","odd_draw","odd_away",
            "odd_btts_yes","odd_btts_no",
            "odd_ou25_over","odd_ou25_under",
            "odd_ou15_over","odd_ou15_under",
            "odds_source"]

    result = [dict(zip(cols, r)) for r in prod_rows + hist_rows]
    result.sort(key=lambda x: x["date"])
    logger.info(f"Loaded {len(result):,} validation fixtures "
                f"({len(prod_rows)} production / {len(hist_rows)} fdco)")
    return result


# ── Fast O(log n) standings lookup with prefix sums ───────────────────────────

class StandingsCache:
    """
    Pre-computes cumulative GF/GA/pts for each (league_id, team_id) pair
    using sorted date arrays + numpy prefix sums, enabling O(log n) lookup.

    Rank is computed among same-season teams only — this bounds the iteration
    to ~20 teams per league-season, not the 200+ that accumulate across all
    historical seasons in international/cup competitions.
    """

    def __init__(self):
        self._dates:    dict[tuple, list]       = {}
        self._cum_gf:   dict[tuple, np.ndarray] = {}
        self._cum_ga:   dict[tuple, np.ndarray] = {}
        self._cum_pts:  dict[tuple, np.ndarray] = {}
        # (league_id, season) → set of team_ids; bounded to ~20 per entry
        self._ls_teams: dict[tuple, set]        = defaultdict(set)

    def build(self, fixtures: List[dict]) -> None:
        raw: dict[tuple, list] = defaultdict(list)
        for f in fixtures:
            lid, hid, aid = f["league_id"], f["home_team_id"], f["away_team_id"]
            season = f.get("season", 0)
            hg, ag = f["goals_home"], f["goals_away"]
            h_pts = 3 if hg > ag else (1 if hg == ag else 0)
            a_pts = 3 if ag > hg else (1 if ag == hg else 0)
            for (tid, gf, ga, pts) in [(hid, hg, ag, h_pts), (aid, ag, hg, a_pts)]:
                raw[(lid, tid)].append((f["date"], gf, ga, pts))
            self._ls_teams[(lid, season)].add(hid)
            self._ls_teams[(lid, season)].add(aid)

        for key, events in raw.items():
            events.sort(key=lambda x: x[0])
            self._dates[key]   = [e[0] for e in events]
            self._cum_gf[key]  = np.cumsum([e[1] for e in events])
            self._cum_ga[key]  = np.cumsum([e[2] for e in events])
            self._cum_pts[key] = np.cumsum([e[3] for e in events])

        n_ls = len(self._ls_teams)
        avg_teams = sum(len(v) for v in self._ls_teams.values()) / max(n_ls, 1)
        logger.info(f"StandingsCache: {len(raw):,} (league, team) pairs; "
                    f"{n_ls:,} (league, season) combos; avg {avg_teams:.1f} teams/league-season")

    def get_stats(self, lid: int, tid: int, before_date: str):
        key = (lid, tid)
        if key not in self._dates:
            return DEFAULT_GF, DEFAULT_GA, 0.0
        idx = bisect_left(self._dates[key], before_date)
        if idx == 0:
            return DEFAULT_GF, DEFAULT_GA, 0.0
        return (
            float(self._cum_gf[key][idx - 1]) or DEFAULT_GF,
            float(self._cum_ga[key][idx - 1]) or DEFAULT_GA,
            float(self._cum_pts[key][idx - 1]),
        )

    def get_rank(self, lid: int, tid: int, season: int, before_date: str) -> int:
        """Rank within same-season teams only — bounded to ~20 iterations."""
        target_pts = self.get_stats(lid, tid, before_date)[2]
        rank = 1
        for oid in self._ls_teams.get((lid, season), set()):
            if oid != tid:
                if self.get_stats(lid, oid, before_date)[2] > target_pts:
                    rank += 1
        return rank

    def build_standings_features(self, f: dict) -> np.ndarray:
        """9 standings-derived features (same as V3 baseline)."""
        lid, date = f["league_id"], f["date"]
        hid, aid  = f["home_team_id"], f["away_team_id"]
        season    = f.get("season", 0)
        h_gf, h_ga, _ = self.get_stats(lid, hid, date)
        a_gf, a_ga, _ = self.get_stats(lid, aid, date)
        h_rank = self.get_rank(lid, hid, season, date)
        a_rank = self.get_rank(lid, aid, season, date)
        return np.array([
            h_rank, a_rank,
            h_gf - h_ga, a_gf - a_ga,
            h_gf, a_gf, h_ga, a_ga,
            abs(h_rank - a_rank),
        ], dtype=float)


# ── Labels ────────────────────────────────────────────────────────────────────

def h2h_label(f):  return {"H": 0, "D": 1, "A": 2}.get(f["outcome"], -1)
def btts_label(f): return 1 if (f["goals_home"] >= 1 and f["goals_away"] >= 1) else 0
def ou25_label(f): return 1 if (f["goals_home"] + f["goals_away"]) > 2.5 else 0
def ou15_label(f): return 1 if (f["goals_home"] + f["goals_away"]) > 1.5 else 0


# ── Model ─────────────────────────────────────────────────────────────────────

def fit_lgbm(market: str, X: np.ndarray, y: np.ndarray):
    if len(X) < 100:
        return None
    if market == "h2h":
        params = dict(n_estimators=300, num_leaves=31, learning_rate=0.05,
                      objective="multiclass", num_class=3,
                      n_jobs=4, verbose=-1, random_state=42)
    else:
        params = dict(n_estimators=300, num_leaves=31, learning_rate=0.05,
                      objective="binary", n_jobs=4, verbose=-1, random_state=42)
    m = lgb.LGBMClassifier(**params)
    m.fit(X, y)
    return m


# ── Platt calibration ─────────────────────────────────────────────────────────

def _logit(p: float) -> float:
    return float(np.log(max(1e-7, min(1 - 1e-7, float(p))) /
                        (1 - max(1e-7, min(1 - 1e-7, float(p))))))


def fit_platt(probs: list, labels: list):
    if len(probs) < 10 or len(np.unique(labels)) < 2:
        return None
    X_cal = np.array([[_logit(p)] for p in probs])
    lr = LogisticRegression(solver="lbfgs", max_iter=1000)
    lr.fit(X_cal, labels)
    return lr


def apply_platt(cal, p: float) -> float:
    if cal is None:
        return p
    try:
        return float(cal.predict_proba(np.array([[_logit(p)]]))[0][1])
    except Exception:
        return p


# ── EV / Kelly ─────────────────────────────────────────────────────────────────

def compute_ev(p: float, odds: float) -> float:
    return p * odds - 1.0


def compute_kelly(p: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - (1 - p)) / b) * KELLY_FRACTION


# ── Bootstrap CI ─────────────────────────────────────────────────────────────

def bootstrap_roi_ci(pnls: list, n: int = N_BOOTSTRAP) -> Tuple[float, float]:
    if len(pnls) < 2:
        return (float("nan"), float("nan"))
    arr = np.array(pnls)
    rng = np.random.default_rng(42)
    means = [np.mean(rng.choice(arr, size=len(arr), replace=True)) for _ in range(n)]
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


# ── Market configs ────────────────────────────────────────────────────────────

MARKET_CONFIGS = {
    "h2h": {
        "label_fn": h2h_label,
        "outcomes": [
            # For multiclass h2h: label_fn returns 0/1/2, Shin idx matches — win_label == idx
            {"name": "H", "odds_key": "odd_home", "all_odds_keys": ["odd_home","odd_draw","odd_away"], "idx": 0, "win_label": 0},
            {"name": "D", "odds_key": "odd_draw", "all_odds_keys": ["odd_home","odd_draw","odd_away"], "idx": 1, "win_label": 1},
            {"name": "A", "odds_key": "odd_away", "all_odds_keys": ["odd_home","odd_draw","odd_away"], "idx": 2, "win_label": 2},
        ],
        "get_prob": lambda probs, idx: probs[idx],
    },
    "btts": {
        "label_fn": btts_label,
        # btts_label returns 1 for yes — win_label=1; idx=0 maps Shin to odd_btts_yes = P(yes)
        "outcomes": [
            {"name": "yes", "odds_key": "odd_btts_yes",
             "all_odds_keys": ["odd_btts_yes","odd_btts_no"], "idx": 0, "win_label": 1},
        ],
        "get_prob": lambda probs, idx: probs[1],
    },
    "ou25": {
        "label_fn": ou25_label,
        # ou25_label returns 1 for over — win_label=1; idx=0 maps Shin to odd_ou25_over = P(over)
        "outcomes": [
            {"name": "over", "odds_key": "odd_ou25_over",
             "all_odds_keys": ["odd_ou25_over","odd_ou25_under"], "idx": 0, "win_label": 1},
        ],
        "get_prob": lambda probs, idx: probs[1],
    },
    "ou15": {
        "label_fn": ou15_label,
        # ou15_label returns 1 for over — win_label=1; idx=0 maps Shin to odd_ou15_over = P(over)
        "outcomes": [
            {"name": "over", "odds_key": "odd_ou15_over",
             "all_odds_keys": ["odd_ou15_over","odd_ou15_under"], "idx": 0, "win_label": 1},
        ],
        "get_prob": lambda probs, idx: probs[1],
    },
}


# ── Pre-compute all features once per N value ─────────────────────────────────

def precompute_features_both(fixtures: List[dict], sc: StandingsCache,
                             wave1: FeatureBuilder) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute 29-dim feature vectors for N=5 and N=10 in a single pass.
    Standings (the slow O(log n) rank lookup) computed once per fixture.
    Returns (X_n5, X_n10) each of shape (len(fixtures), 29).
    """
    n = len(fixtures)
    out5  = np.zeros((n, 29), dtype=float)
    out10 = np.zeros((n, 29), dtype=float)
    for i, f in enumerate(fixtures):
        if i % 50000 == 0:
            logger.info(f"  Features: {i:,}/{n:,}")
        try:
            standings   = sc.build_standings_features(f)
            w1_5, w1_10 = wave1.build_pair(f)
            out5[i]  = np.concatenate([standings, w1_5])
            out10[i] = np.concatenate([standings, w1_10])
        except Exception:
            pass
    return out5, out10


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate_window(window: dict,
                    all_train: List[dict], X_train_all: np.ndarray,
                    val_fix: List[dict],   X_val_all: np.ndarray) -> dict:
    """
    Filter pre-computed feature arrays by date window, train, predict, score.
    Returns {market: [bet_dict, ...]}
    """
    test_start = window["test_start"]
    test_end   = window["test_end"]
    wname      = window["name"]

    # Training mask: strictly before test window
    train_mask   = np.array([f["date"] < test_start for f in all_train], dtype=bool)
    true_indices = np.where(train_mask)[0]

    # Sub-sample to MAX_TRAIN_SAMPLES (most recent fixtures preferred)
    if len(true_indices) > MAX_TRAIN_SAMPLES:
        rng  = np.random.default_rng(42)
        # Keep all from the last 2 years (temporal recency), fill rest randomly
        cutoff_date = all_train[true_indices[-1]]["date"][:4]  # year string
        recent_yr   = str(int(cutoff_date) - 1)  # previous year
        recent_mask = np.array(
            [all_train[i]["date"] >= f"{recent_yr}-01-01" for i in true_indices],
            dtype=bool
        )
        recent_idx = true_indices[recent_mask]
        older_idx  = true_indices[~recent_mask]
        n_need = max(0, MAX_TRAIN_SAMPLES - len(recent_idx))
        if n_need > 0 and len(older_idx) > 0:
            sampled_older = rng.choice(older_idx, size=min(n_need, len(older_idx)), replace=False)
            true_indices = np.sort(np.concatenate([recent_idx, sampled_older]))
        else:
            true_indices = recent_idx[:MAX_TRAIN_SAMPLES]

    # Calibration split: last 30% of (sub-sampled) training set
    n_train = len(true_indices)
    split_n = int(n_train * (1 - CALIB_HOLDOUT_FRAC))
    fit_idx = true_indices[:split_n]
    cal_idx = true_indices[split_n:]

    X_fit  = X_train_all[fit_idx]
    X_cal  = X_train_all[cal_idx]
    fit_fx = [all_train[i] for i in fit_idx]
    cal_fx = [all_train[i] for i in cal_idx]

    # Validation mask: within test window
    val_mask = np.array([test_start <= f["date"] < test_end for f in val_fix], dtype=bool)
    X_test   = X_val_all[val_mask]
    test_fx  = [val_fix[i] for i in np.where(val_mask)[0]]

    logger.info(f"Window {wname}: {len(fit_fx):,} fit / {len(cal_fx):,} cal / "
                f"{len(test_fx):,} test")

    market_bets: dict[str, list] = {m: [] for m in MARKET_CONFIGS}

    for market, cfg in MARKET_CONFIGS.items():
        lbl_fn = cfg["label_fn"]

        y_fit = np.array([lbl_fn(f) for f in fit_fx])
        mask  = y_fit >= 0
        if mask.sum() < 100:
            continue

        y_cal = np.array([lbl_fn(f) for f in cal_fx])

        model = fit_lgbm(market, X_fit[mask], y_fit[mask])
        if model is None:
            continue

        # Per-outcome Platt calibrators
        # win_label: the label_fn return value that means "this outcome won"
        # idx: Shin de-vigged probability index (maps to first odd in all_odds_keys)
        # These differ for binary markets (e.g. ou25 over: idx=0 → P(over), win_label=1 → label==1 means over)
        calibrators: dict[int, object] = {}
        for oc in cfg["outcomes"]:
            idx      = oc["idx"]
            win_lbl  = oc["win_label"]
            cal_probs = []
            for i, f in enumerate(cal_fx):
                try:
                    raw = model.predict_proba([X_cal[i]])[0]
                    cal_probs.append(cfg["get_prob"](raw, idx))
                except Exception:
                    cal_probs.append(0.5)
            cal_labels_bin = [1 if lbl_fn(f) == win_lbl else 0 for f in cal_fx]
            calibrators[win_lbl] = fit_platt(cal_probs, cal_labels_bin)

        # Predict on test
        for i, f in enumerate(test_fx):
            try:
                raw_probs = model.predict_proba([X_test[i]])[0]
            except Exception:
                continue

            for oc in cfg["outcomes"]:
                odds_key = oc["odds_key"]
                oc_idx   = oc["idx"]
                win_lbl  = oc["win_label"]

                odds = f.get(odds_key)
                if odds is None or odds < 1.6:
                    continue

                # fdco has no btts/ou15 odds
                if f["odds_source"] == "fdco" and odds_key in (
                    "odd_btts_yes", "odd_btts_no", "odd_ou15_over", "odd_ou15_under"
                ):
                    continue

                p_model   = cfg["get_prob"](raw_probs, oc_idx)
                p_cal     = apply_platt(calibrators.get(win_lbl), p_model)
                all_odds  = [f.get(k) for k in oc["all_odds_keys"]]
                p_blended, p_market = market_blend(p_cal, all_odds, oc_idx)

                ev = compute_ev(p_blended, odds)
                if ev <= BOT_MIN_EV:
                    continue

                actual_label = lbl_fn(f)
                if actual_label < 0:
                    continue

                won = (actual_label == win_lbl)
                pnl = odds - 1 if won else -1.0

                market_bets[market].append({
                    "fixture_id": f["id"],
                    "date":       f["date"],
                    "outcome":    oc["name"],
                    "odds":       odds,
                    "ev":         ev,
                    "p_blended":  p_blended,
                    "p_market":   p_market,
                    "won":        won,
                    "pnl":        pnl,
                    "window":     wname,
                    "odds_source": f["odds_source"],
                })

    return market_bets


# ── Scoring + success bar ─────────────────────────────────────────────────────

def score_bets(bets: list, label: str, n_test_fixtures: int,
               n_slots_per_fix: int) -> dict:
    if not bets:
        return {"label": label, "n_bets": 0,
                "n_test_fixtures": n_test_fixtures,
                "roi": float("nan"), "ci_lo": float("nan"),
                "ci_hi": float("nan"), "ev_pass_rate": 0.0,
                "win_rate": float("nan"), "avg_odds": float("nan"),
                "avg_ev": float("nan"), "avg_p_blended": float("nan")}
    pnls   = [b["pnl"] for b in bets]
    wins   = sum(1 for b in bets if b["won"])
    total  = len(bets)
    total_pnl = sum(pnls)
    roi    = total_pnl / total if total > 0 else 0.0
    ci_lo, ci_hi = bootstrap_roi_ci(pnls)
    n_slots = n_test_fixtures * n_slots_per_fix
    return {
        "label":           label,
        "n_bets":          total,
        "n_test_fixtures": n_test_fixtures,
        "n_market_slots":  n_slots,
        "ev_pass_rate":    total / n_slots if n_slots > 0 else 0.0,
        "win_rate":        wins / total,
        "avg_odds":        float(np.mean([b["odds"] for b in bets])),
        "avg_ev":          float(np.mean([b["ev"] for b in bets])),
        "avg_p_blended":   float(np.mean([b["p_blended"] for b in bets])),
        "roi":             roi,
        "ci_lo":           ci_lo,
        "ci_hi":           ci_hi,
    }


def apply_success_bar(market: str, window_results: list) -> dict:
    MIN_BETS = 500
    passing = []
    for r in window_results:
        n_ok  = r["n_bets"] >= MIN_BETS
        ci_ok = r.get("ci_lo", float("nan")) > 0
        if n_ok and ci_ok:
            passing.append(r["label"])
    n_pass = len(passing)
    return {
        "market":          market,
        "n_windows_pass":  n_pass,
        "windows_passing": passing,
        "min_bets_met_per_window": {
            r["label"]: (r["n_bets"] >= MIN_BETS, r["n_bets"])
            for r in window_results
        },
        "ci_excludes_zero_per_window": {
            r["label"]: (r.get("ci_lo", float("nan")) > 0, r.get("ci_lo"))
            for r in window_results
        },
        "overall_pass": n_pass >= 2,
        "verdict": "PASS" if n_pass >= 2 else "FAIL",
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info("=== Walk-Forward Backtest V4 (Wave 1 + Fast Standings) ===")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"ATTACH '{HIST_DB_PATH}' AS hist")

    all_train = load_all_training_fixtures(conn)
    val_fix   = load_validation_fixtures(conn)

    # Build fast standings cache (one-time cost)
    logger.info("Building StandingsCache (O(log n) prefix sums)...")
    sc = StandingsCache()
    sc.build(all_train + val_fix)

    # Build Wave 1 feature cache (one-time cost)
    logger.info("Loading Wave 1 FeatureBuilder...")
    wave1 = FeatureBuilder(conn, n_rolling=5, min_season=2018)
    wave1.load()
    logger.info(f"  Teams: {len(wave1._team_form):,}  "
                f"Leagues: {len(wave1._league_history):,}  "
                f"H2H pairs: {len(wave1._h2h):,}")

    n_train = len(all_train)
    n_val   = len(val_fix)

    # Precompute both N=5 and N=10 in one pass — standings rank computed once
    logger.info(f"\nPre-computing features (N=5 and N=10 combined) for {n_train:,} training fixtures...")
    X_train_n5, X_train_n10 = precompute_features_both(all_train, sc, wave1)
    logger.info(f"  Training matrices: N5 {X_train_n5.shape}, N10 {X_train_n10.shape}")

    logger.info(f"Pre-computing features (N=5 and N=10 combined) for {n_val:,} validation fixtures...")
    X_val_n5, X_val_n10 = precompute_features_both(val_fix, sc, wave1)
    logger.info(f"  Validation matrices: N5 {X_val_n5.shape}, N10 {X_val_n10.shape}")

    X_by_n = {5: (X_train_n5, X_val_n5), 10: (X_train_n10, X_val_n10)}

    results: dict = {}

    for n_roll in ROLLING_NS:
        label_n = f"N{n_roll}"
        X_train_all, X_val_all = X_by_n[n_roll]
        logger.info(f"\n{'='*60}")
        logger.info(f"--- N={n_roll} window simulations ---")

        results[label_n] = {}
        all_window_bets: dict[str, list] = {m: [] for m in MARKET_CONFIGS}

        for window in WINDOWS:
            wname = window["name"]
            logger.info(f"\n--- Window {wname} (N={n_roll}) ---")

            window_bets = simulate_window(
                window, all_train, X_train_all, val_fix, X_val_all
            )

            test_fix_count = sum(1 for f in val_fix
                                 if window["test_start"] <= f["date"] < window["test_end"])

            for market in MARKET_CONFIGS:
                bets   = window_bets.get(market, [])
                slots  = MARKET_SLOTS[market]
                scored = score_bets(bets, f"{wname}", test_fix_count, slots)
                scored["window"] = wname
                scored["market"] = market

                key = f"{label_n}_{market}"
                if key not in results[label_n]:
                    results[label_n][key] = {"windows": []}
                results[label_n][key]["windows"].append(scored)

                for b in bets:
                    all_window_bets[market].append(b)

                logger.info(
                    f"  {market:5s}: {scored['n_bets']:4d} bets | "
                    f"ROI {scored['roi']*100:+.1f}% "
                    f"[{scored['ci_lo']*100:+.1f}%, {scored['ci_hi']*100:+.1f}%]"
                )

        # Aggregate + success bar
        for market in MARKET_CONFIGS:
            key = f"{label_n}_{market}"
            all_bets = all_window_bets[market]
            n_total  = sum(r["n_test_fixtures"] for r in results[label_n][key]["windows"])
            slots    = MARKET_SLOTS[market]
            agg      = score_bets(all_bets, f"{label_n}_{market}_ALL", n_total, slots)
            agg["market"] = market
            results[label_n][key]["aggregate"] = agg
            results[label_n][key]["success_bar"] = apply_success_bar(
                market, results[label_n][key]["windows"]
            )

        logger.info(f"\n=== N={n_roll} AGGREGATE ===")
        header = f"  {'Market':6s}  {'Bets':>5s}  {'Pass%':>6s}  {'ROI%':>7s}  {'95% CI':>18s}  {'AvgEV':>7s}  Bar"
        logger.info(header)
        for market in MARKET_CONFIGS:
            key = f"{label_n}_{market}"
            agg = results[label_n][key]["aggregate"]
            bar = results[label_n][key]["success_bar"]["verdict"]
            n_total = sum(r["n_test_fixtures"] for r in results[label_n][key]["windows"])
            slots   = MARKET_SLOTS[market]
            pct     = agg["n_bets"] / (n_total * slots) * 100 if n_total > 0 else 0
            logger.info(
                f"  {market:6s}  {agg['n_bets']:>5d}  {pct:>5.1f}%  "
                f"{agg['roi']*100:>+6.1f}%  "
                f"[{agg['ci_lo']*100:>+6.1f}%, {agg['ci_hi']*100:>+6.1f}%]  "
                f"{agg['avg_ev']*100:>6.1f}%  {bar}"
            )

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    logger.info(f"\nResults written to {OUTPUT_PATH}")

    # Print final table
    print("\n" + "="*90)
    print("SUMMARY TABLE")
    print("="*90)
    for n_roll in ROLLING_NS:
        label_n = f"N{n_roll}"
        print(f"\n{'─'*90}")
        print(f"N={n_roll} rolling window | Phase 2 Wave 1 features (29 total = 9 standings + 20 Wave1)")
        print(f"{'─'*90}")
        print(f"  {'Market':6s}  {'Bets':>5s}  {'Pass%':>6s}  {'ROI%':>7s}  {'95% CI':>18s}  "
              f"{'AvgEV':>7s}  {'AvgOdds':>8s}  {'WinR':>6s}  {'Bar':>4s}")
        for market in MARKET_CONFIGS:
            key = f"{label_n}_{market}"
            if key not in results[label_n]:
                continue
            agg = results[label_n][key]["aggregate"]
            bar = results[label_n][key]["success_bar"]["verdict"]
            n_test_total = sum(r["n_test_fixtures"]
                               for r in results[label_n][key]["windows"])
            slots = MARKET_SLOTS[market]
            pct   = agg["n_bets"] / (n_test_total * slots) * 100 if n_test_total > 0 else 0
            print(f"  {market:6s}  {agg['n_bets']:>5d}  {pct:>5.1f}%  "
                  f"{agg['roi']*100:>+6.1f}%  "
                  f"[{agg['ci_lo']*100:>+6.1f}%, {agg['ci_hi']*100:>+6.1f}%]  "
                  f"{agg['avg_ev']*100:>6.1f}%  {agg['avg_odds']:>8.3f}  "
                  f"{agg['win_rate']*100:>5.1f}%  {bar}")

        print(f"\nSuccess bar per window:")
        for market in MARKET_CONFIGS:
            key = f"{label_n}_{market}"
            if key not in results[label_n]:
                continue
            sb = results[label_n][key]["success_bar"]
            for r in results[label_n][key]["windows"]:
                n_ok  = r["n_bets"] >= 500
                ci_ok = r.get("ci_lo", float("nan")) > 0
                ci_lo_val = r.get("ci_lo", float("nan"))
                ci_str = "CI>0✓" if ci_ok else f"CI=[{ci_lo_val*100:.1f}%]✗"
                bets_str = "≥500✓" if n_ok else f"{r['n_bets']}✗"
                print(f"    {market:6s} {r['window']:8s}: {bets_str:7s} | {ci_str}")
    print()


if __name__ == "__main__":
    main()
