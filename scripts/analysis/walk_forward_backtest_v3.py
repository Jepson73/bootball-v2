#!/usr/bin/env python3
# ruff: noqa
import warnings
warnings.filterwarnings("ignore", message="X does not have valid feature names")
"""
Walk-forward backtest V3 — production-formula (Shin market blend).

Supersedes V2 for Task L of Phase 1d. Key changes vs V2:
  1. Correct EV formula:  ev = p * odds - 1   (was p*(odds+1)-1 in V2 — bug fixed)
  2. Adds market blend step:  p_blended = 0.35 * p_cal + 0.65 * Shin(market_odds)
     This matches the actual production formula in unified_prediction_service.py.
  3. Reports four modes for comparison:
       - oof_noBlend : Platt OOF calibration, correct EV formula, NO blend (new baseline)
       - oof_blend   : Platt OOF calibration + Shin blend (production formula)
       - raw_noBlend : uncalibrated, correct EV formula
       - raw_blend   : uncalibrated + Shin blend

Output: scripts/analysis/backtest_results_v3.json
"""

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import lightgbm as lgb
import numpy as np
from scipy.optimize import brentq
from sklearn.linear_model import LogisticRegression
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backtest_v3")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "football.db"
OUTPUT_PATH = Path(__file__).resolve().parent / "backtest_results_v3.json"

BOT_MIN_EV = 0.05
MODEL_WEIGHT = 0.35          # same as src/calibration/market_blend.py
KELLY_FRACTION = 0.25
MIN_BET = 10.0
STARTING_BANKROLL = 1000.0
DEFAULT_RANK = 15
DEFAULT_GF = 1.0
DEFAULT_GA = 1.0
CALIB_HOLDOUT_FRAC = 0.30

WINDOW_STARTS = [datetime(2026, 4, 15), datetime(2026, 5, 1), datetime(2026, 6, 1)]
WINDOW_END = datetime(2026, 6, 17)

N_CLUSTERS = 3

CAL_MODES = ["raw_noBlend", "raw_blend", "oof_noBlend", "oof_blend"]


# ── Shin de-vigging ───────────────────────────────────────────────────────────

def shin_probabilities(odds: list) -> list:
    """Shin (1993) de-vigging; returns true probabilities summing to 1."""
    raw = np.array([1.0 / o for o in odds])
    W = raw.sum()
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


def market_blend(p_model: float, all_odds: list, outcome_idx: int) -> tuple:
    """
    Blend model probability with Shin-devigged market probability.
    Returns (p_blended, p_market). Falls back to p_model if odds unusable.
    """
    if not all_odds or any(o is None or o < 1.01 for o in all_odds):
        return p_model, None
    try:
        devigged = shin_probabilities(all_odds)
        p_market = devigged[outcome_idx]
        p_blended = MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * p_market
        return p_blended, p_market
    except Exception:
        return p_model, None


# ── Data loading ──────────────────────────────────────────────────────────────

def load_all_fixtures() -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, league_id, season, home_team_id, away_team_id,
               date, goals_home, goals_away, outcome
        FROM fixtures
        WHERE season = 2025 AND status = 'FT'
          AND goals_home IS NOT NULL AND goals_away IS NOT NULL
          AND outcome IS NOT NULL
        ORDER BY date ASC
    """).fetchall()
    cols = ["id","league_id","season","home_team_id","away_team_id",
            "date","goals_home","goals_away","outcome"]
    conn.close()
    result = [dict(zip(cols, r)) for r in rows]
    logger.info(f"Loaded {len(result):,} training fixtures")
    return result


def load_odds_fixtures() -> List[dict]:
    """Load FT test fixtures with all four markets' odds."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT
            f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
            f.date, f.goals_home, f.goals_away, f.outcome,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_home END)          AS odd_home,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_draw END)          AS odd_draw,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_away END)          AS odd_away,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_yes END)      AS odd_btts_yes,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_no END)       AS odd_btts_no,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over END)    AS odd_ou25_over,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under END)   AS odd_ou25_under,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over15 END)  AS odd_ou15_over,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under15 END) AS odd_ou15_under
        FROM fixtures f
        JOIN fixture_odds fo ON fo.fixture_id = f.id
        WHERE f.season = 2025 AND f.status = 'FT'
          AND f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
          AND f.outcome IS NOT NULL
        GROUP BY f.id
        ORDER BY f.date ASC
    """).fetchall()
    cols = ["id","league_id","season","home_team_id","away_team_id",
            "date","goals_home","goals_away","outcome",
            "odd_home","odd_draw","odd_away",
            "odd_btts_yes","odd_btts_no",
            "odd_ou25_over","odd_ou25_under",
            "odd_ou15_over","odd_ou15_under"]
    conn.close()
    result = [dict(zip(cols, r)) for r in rows]
    logger.info(f"Loaded {len(result):,} fixtures with odds (test candidates)")
    return result


# ── Features ──────────────────────────────────────────────────────────────────

def build_team_history(fixtures):
    history = {}
    for f in fixtures:
        lid, hid, aid = f["league_id"], f["home_team_id"], f["away_team_id"]
        hg, ag = f["goals_home"], f["goals_away"]
        h_pts = 3 if hg > ag else (1 if hg == ag else 0)
        a_pts = 3 if ag > hg else (1 if ag == hg else 0)
        for (tid, gf, ga, pts) in [(hid, hg, ag, h_pts), (aid, ag, hg, a_pts)]:
            key = (lid, tid)
            if key not in history:
                history[key] = []
            history[key].append((f["date"], gf, ga, pts))
    for key in history:
        history[key].sort(key=lambda x: x[0])
    return history


def get_team_stats_before(history, lid, tid, date_str):
    gf = ga = pts = 0.0
    for (d, g, a, p) in history.get((lid, tid), []):
        if d >= date_str:
            break
        gf += g; ga += a; pts += p
    return gf or DEFAULT_GF, ga or DEFAULT_GA, pts


def compute_rank(history, lid, tid, date_str, all_team_ids):
    target_pts = get_team_stats_before(history, lid, tid, date_str)[2]
    return 1 + sum(1 for oid in all_team_ids if oid != tid and
                   get_team_stats_before(history, lid, oid, date_str)[2] > target_pts)


def build_features(f, history, league_teams):
    lid, date, hid, aid = f["league_id"], f["date"], f["home_team_id"], f["away_team_id"]
    teams = league_teams.get(lid, [])
    h_gf, h_ga, _ = get_team_stats_before(history, lid, hid, date)
    a_gf, a_ga, _ = get_team_stats_before(history, lid, aid, date)
    h_rank = compute_rank(history, lid, hid, date, teams)
    a_rank = compute_rank(history, lid, aid, date, teams)
    return np.array([h_rank, a_rank, h_gf - h_ga, a_gf - a_ga,
                     h_gf, a_gf, h_ga, a_ga, abs(h_rank - a_rank)], dtype=float)


# ── Labels ────────────────────────────────────────────────────────────────────

def h2h_label(f):
    return {"H": 0, "D": 1, "A": 2}.get(f["outcome"], -1)

def btts_label(f):
    return 1 if (f["goals_home"] >= 1 and f["goals_away"] >= 1) else 0

def ou25_label(f):
    return 1 if (f["goals_home"] + f["goals_away"]) > 2.5 else 0

def ou15_label(f):
    return 1 if (f["goals_home"] + f["goals_away"]) > 1.5 else 0


# ── Model fitting ─────────────────────────────────────────────────────────────

def fit_lgbm(market, X, y):
    if len(X) < 50:
        return None
    if market == "h2h":
        params = dict(n_estimators=200, num_leaves=31, learning_rate=0.05,
                      objective="multiclass", num_class=3, n_jobs=2, verbose=-1, random_state=42)
    else:
        params = dict(n_estimators=200, num_leaves=31, learning_rate=0.05,
                      objective="binary", n_jobs=2, verbose=-1, random_state=42)
    m = lgb.LGBMClassifier(**params)
    m.fit(X, y)
    return m


# ── Platt calibration ─────────────────────────────────────────────────────────

def _logit(p):
    p = max(1e-7, min(1 - 1e-7, float(p)))
    return np.log(p / (1 - p))


def fit_platt(probs, labels):
    if len(probs) < 10 or len(np.unique(labels)) < 2:
        return None
    X_cal = np.array([[_logit(p)] for p in probs])
    lr = LogisticRegression(solver="lbfgs", max_iter=1000)
    lr.fit(X_cal, labels)
    return lr


def apply_platt(calibrator, p):
    if calibrator is None:
        return p
    try:
        return float(calibrator.predict_proba(np.array([[_logit(p)]]))[0][1])
    except Exception:
        return p


# ── EV / Kelly (CORRECT formulas) ─────────────────────────────────────────────

def compute_ev(p: float, odds: float) -> float:
    """Standard expected value formula: EV = p*d - 1."""
    return p * odds - 1.0


def compute_kelly(p: float, odds: float) -> float:
    """Fractional Kelly stake as fraction of bankroll."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - (1 - p)) / b) * KELLY_FRACTION


# ── League heterogeneity + clustering ─────────────────────────────────────────

def compute_league_metrics(fixtures):
    stats: Dict[int, dict] = {}
    for f in fixtures:
        lid = f["league_id"]
        if lid not in stats:
            stats[lid] = {"goals": [], "btts": [], "team_wins": defaultdict(int), "n": 0}
        total_goals = f["goals_home"] + f["goals_away"]
        stats[lid]["goals"].append(total_goals)
        stats[lid]["btts"].append(1 if f["goals_home"] >= 1 and f["goals_away"] >= 1 else 0)
        winner = f["home_team_id"] if f["goals_home"] > f["goals_away"] else (
            f["away_team_id"] if f["goals_away"] > f["goals_home"] else None)
        if winner:
            stats[lid]["team_wins"][winner] += 1
        stats[lid]["n"] += 1

    metrics = {}
    for lid, s in stats.items():
        if s["n"] < 10:
            continue
        wins = list(s["team_wins"].values())
        total_wins = sum(wins) or 1
        hhi = sum((w / total_wins) ** 2 for w in wins)
        metrics[lid] = {
            "avg_goals": float(np.mean(s["goals"])),
            "btts_rate": float(np.mean(s["btts"])),
            "hhi": float(hhi),
        }
    return metrics


def cluster_leagues(metrics):
    if len(metrics) < N_CLUSTERS:
        return {lid: 0 for lid in metrics}, {}
    lids = list(metrics.keys())
    X = np.array([[metrics[l]["avg_goals"], metrics[l]["btts_rate"], metrics[l]["hhi"]] for l in lids])
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    labels = km.fit_predict(Xs)
    cluster_map = {lid: int(labels[i]) for i, lid in enumerate(lids)}
    summary = {}
    for c in range(N_CLUSTERS):
        c_lids = [l for l in lids if cluster_map[l] == c]
        if c_lids:
            summary[c] = {
                "n_leagues": len(c_lids),
                "avg_goals_mean": np.mean([metrics[l]["avg_goals"] for l in c_lids]),
                "btts_rate_mean": np.mean([metrics[l]["btts_rate"] for l in c_lids]),
                "hhi_mean": np.mean([metrics[l]["hhi"] for l in c_lids]),
            }
    return cluster_map, summary


# ── Calibration data ──────────────────────────────────────────────────────────

def build_calibration_data(model, market, train_fixtures, feature_map, split_frac=CALIB_HOLDOUT_FRAC):
    if model is None or not train_fixtures:
        return np.array([]), np.array([])
    n = len(train_fixtures)
    split_idx = int(n * (1 - split_frac))
    calib_fixtures = train_fixtures[split_idx:]
    valid = [f for f in calib_fixtures if feature_map.get(f["id"]) is not None]
    if not valid:
        return np.array([]), np.array([])
    X_batch = np.array([feature_map[f["id"]] for f in valid])
    all_raw = model.predict_proba(X_batch)
    probs_list, labels_list = [], []
    for i, f in enumerate(valid):
        row = all_raw[i]
        if market == "h2h":
            best_idx = int(np.argmax(row))
            p = row[best_idx]
            label = 1 if h2h_label(f) == best_idx else 0
        elif market == "btts":
            p = row[1]; label = btts_label(f)
        elif market == "ou25":
            p = row[1]; label = ou25_label(f)
        elif market == "ou15":
            p = row[1]; label = ou15_label(f)
        else:
            continue
        probs_list.append(p)
        labels_list.append(label)
    return np.array(probs_list), np.array(labels_list)


def fit_oof_calibrator(market, model, train_fixtures, feature_map):
    probs, labels = build_calibration_data(model, market, train_fixtures, feature_map,
                                           split_frac=CALIB_HOLDOUT_FRAC)
    return fit_platt(probs, labels) if len(probs) >= 20 else None


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate_bets(models, calibrators, test_fixtures, feature_map, bankroll):
    """
    calibrators: {market: LogisticRegression | None}  (OOF calibrators)
    Returns: {mode: [bet_dict, ...]}
    """
    all_bets: Dict[str, List[dict]] = {m: [] for m in CAL_MODES}

    # Define market configurations: (market_name, outcome_labels, all_odds_fn, outcome_idx_fn, won_fn, bet_odds_fn)
    def get_market_configs(f):
        return [
            {
                "market": "h2h",
                "outcomes": [
                    ("H", [f.get("odd_home"), f.get("odd_draw"), f.get("odd_away")], 0,
                     f.get("odd_home"), f["outcome"] == "H"),
                    ("D", [f.get("odd_home"), f.get("odd_draw"), f.get("odd_away")], 1,
                     f.get("odd_draw"), f["outcome"] == "D"),
                    ("A", [f.get("odd_home"), f.get("odd_draw"), f.get("odd_away")], 2,
                     f.get("odd_away"), f["outcome"] == "A"),
                ],
                "raw_prob_fn": lambda raw, lbl: raw[{"H":0,"D":1,"A":2}[lbl]],
            },
            {
                "market": "btts",
                "outcomes": [
                    ("Yes", [f.get("odd_btts_yes"), f.get("odd_btts_no")], 0,
                     f.get("odd_btts_yes"), btts_label(f) == 1),
                    ("No",  [f.get("odd_btts_yes"), f.get("odd_btts_no")], 1,
                     f.get("odd_btts_no"),  btts_label(f) == 0),
                ],
                "raw_prob_fn": lambda raw, lbl: raw[1] if lbl == "Yes" else raw[0],
            },
            {
                "market": "ou25",
                "outcomes": [
                    ("Over",  [f.get("odd_ou25_over"), f.get("odd_ou25_under")], 0,
                     f.get("odd_ou25_over"),  ou25_label(f) == 1),
                    ("Under", [f.get("odd_ou25_over"), f.get("odd_ou25_under")], 1,
                     f.get("odd_ou25_under"), ou25_label(f) == 0),
                ],
                "raw_prob_fn": lambda raw, lbl: raw[1] if lbl == "Over" else raw[0],
            },
            {
                "market": "ou15",
                "outcomes": [
                    ("Over",  [f.get("odd_ou15_over"), f.get("odd_ou15_under")], 0,
                     f.get("odd_ou15_over"),  ou15_label(f) == 1),
                    ("Under", [f.get("odd_ou15_over"), f.get("odd_ou15_under")], 1,
                     f.get("odd_ou15_under"), ou15_label(f) == 0),
                ],
                "raw_prob_fn": lambda raw, lbl: raw[1] if lbl == "Over" else raw[0],
            },
        ]

    for f in test_fixtures:
        fid = f["id"]
        feats = feature_map.get(fid)
        if feats is None:
            continue
        X = feats.reshape(1, -1)

        for mconf in get_market_configs(f):
            mkt = mconf["market"]
            model = models.get(mkt)
            if model is None:
                continue
            oof_cal = calibrators.get(mkt)

            try:
                raw_probs = model.predict_proba(X)[0]
            except Exception:
                continue

            for mode in CAL_MODES:
                use_blend = mode.endswith("_blend")
                use_oof = mode.startswith("oof")

                best_ev = -999.0
                best_bet = None

                for (outcome_label, all_mkt_odds, shin_idx, bet_odds, won) in mconf["outcomes"]:
                    if not bet_odds or bet_odds < 1.0:
                        continue

                    raw_p = mconf["raw_prob_fn"](raw_probs, outcome_label)

                    # Step 1: Platt calibration
                    cal_p = apply_platt(oof_cal, raw_p) if use_oof else raw_p

                    # Step 2: Market blend (Shin de-vigging)
                    if use_blend:
                        p_final, p_market = market_blend(cal_p, all_mkt_odds, shin_idx)
                    else:
                        p_final, p_market = cal_p, None

                    ev = compute_ev(p_final, bet_odds)
                    if ev > best_ev:
                        best_ev = ev
                        best_bet = (outcome_label, raw_p, cal_p, p_final, p_market, bet_odds, won)

                if best_bet is None or best_ev < BOT_MIN_EV:
                    continue

                outcome_label, raw_p, cal_p, p_final, p_market, bet_odds, won = best_bet
                stake = compute_kelly(p_final, bet_odds) * bankroll
                if stake < MIN_BET:
                    continue
                pnl = stake * (bet_odds - 1) if won else -stake
                all_bets[mode].append({
                    "fixture_id": fid,
                    "date": f["date"],
                    "market": mkt,
                    "league_id": f["league_id"],
                    "outcome": outcome_label,
                    "our_prob_raw": round(raw_p, 4),
                    "our_prob_cal": round(cal_p, 4),
                    "p_blended": round(p_final, 4),
                    "p_market": round(p_market, 4) if p_market is not None else None,
                    "odds": bet_odds,
                    "ev": round(best_ev, 4),
                    "stake": round(stake, 2),
                    "won": won,
                    "pnl": round(pnl, 2),
                })

    return all_bets


# ── Metrics ───────────────────────────────────────────────────────────────────

def bootstrap_roi(bets, n_boot=2000, seed=42):
    if not bets:
        return 0.0, 0.0, 0.0
    pnls = np.array([b["pnl"] for b in bets])
    stakes = np.array([b["stake"] for b in bets])
    total = stakes.sum()
    if total == 0:
        return 0.0, 0.0, 0.0
    point = pnls.sum() / total
    rng = np.random.default_rng(seed)
    boots = []
    n = len(bets)
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        s = stakes[idx].sum()
        if s > 0:
            boots.append(pnls[idx].sum() / s)
    if not boots:
        return float(point), float(point), float(point)
    boots = np.array(boots)
    return float(point), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def market_summary(bets, all_candidates_by_market=None):
    if not bets:
        return {}
    markets = sorted(set(b["market"] for b in bets))
    result = {}
    for m in markets:
        mb = [b for b in bets if b["market"] == m]
        roi, ci_lo, ci_hi = bootstrap_roi(mb)
        result[m] = {
            "n_bets": len(mb),
            "staked": round(sum(b["stake"] for b in mb), 2),
            "pnl": round(sum(b["pnl"] for b in mb), 2),
            "roi_pct": round(roi * 100, 2),
            "ci_95": [round(ci_lo * 100, 2), round(ci_hi * 100, 2)],
            "win_rate": round(np.mean([1 if b["won"] else 0 for b in mb]), 4),
            "avg_odds": round(np.mean([b["odds"] for b in mb]), 3),
            "avg_ev": round(np.mean([b["ev"] for b in mb]), 4),
            "avg_p_raw": round(np.mean([b["our_prob_raw"] for b in mb]), 4),
            "avg_p_final": round(np.mean([b["p_blended"] for b in mb]), 4),
        }
    return result


# ── Main walk-forward loop ────────────────────────────────────────────────────

def run():
    all_fixtures = load_all_fixtures()
    odds_fixtures = load_odds_fixtures()

    history = build_team_history(all_fixtures)
    league_teams: Dict[int, List[int]] = defaultdict(set)
    for f in all_fixtures:
        league_teams[f["league_id"]].add(f["home_team_id"])
        league_teams[f["league_id"]].add(f["away_team_id"])
    league_teams = {lid: list(tids) for lid, tids in league_teams.items()}

    league_metrics = compute_league_metrics(all_fixtures)
    league_cluster_map, cluster_summary = cluster_leagues(league_metrics)
    logger.info(f"League clusters: {cluster_summary}")

    logger.info("Building feature maps...")
    all_feature_map: Dict[int, np.ndarray] = {}
    for f in all_fixtures:
        v = build_features(f, history, league_teams)
        if v is not None:
            all_feature_map[f["id"]] = v
    odds_feature_map: Dict[int, np.ndarray] = {}
    for f in odds_fixtures:
        v = build_features(f, history, league_teams)
        if v is not None:
            odds_feature_map[f["id"]] = v
    logger.info(f"Feature maps: {len(all_feature_map):,} training, {len(odds_feature_map):,} test")

    odds_fixtures_sorted = sorted(odds_fixtures, key=lambda f: f["date"])

    bets_by_mode: Dict[str, List[dict]] = {m: [] for m in CAL_MODES}

    for w_idx, window_start in enumerate(WINDOW_STARTS):
        window_end = WINDOW_STARTS[w_idx + 1] if w_idx + 1 < len(WINDOW_STARTS) else WINDOW_END
        cutoff = window_start.isoformat()
        window_start_str = window_start.isoformat()
        window_end_str = window_end.isoformat()

        train_fx = [f for f in all_fixtures if f["date"] < cutoff]
        test_fx = [f for f in odds_fixtures_sorted
                   if window_start_str <= f["date"] < window_end_str]

        logger.info(f"Window {w_idx + 1}: train={len(train_fx):,}, test={len(test_fx):,}")

        if not test_fx:
            continue

        # Build point-in-time feature map for test fixtures
        test_feature_map: Dict[int, np.ndarray] = {}
        for f in test_fx:
            v = build_features(f, history, league_teams)
            if v is not None:
                test_feature_map[f["id"]] = v

        # Label arrays for training
        markets = ["h2h", "btts", "ou25", "ou15"]
        label_fns = {
            "h2h": lambda f: h2h_label(f),
            "btts": lambda f: btts_label(f),
            "ou25": lambda f: ou25_label(f),
            "ou15": lambda f: ou15_label(f),
        }

        models = {}
        calibrators = {}

        for market in markets:
            valid_train = [f for f in train_fx if all_feature_map.get(f["id"]) is not None]
            if not valid_train:
                continue
            X = np.array([all_feature_map[f["id"]] for f in valid_train])
            y = np.array([label_fns[market](f) for f in valid_train])

            if market == "h2h":
                valid_mask = y >= 0
                X, y = X[valid_mask], y[valid_mask]
                valid_train = [f for f, m in zip(valid_train, valid_mask) if m]

            model = fit_lgbm(market, X, y)
            models[market] = model
            if model is not None:
                oof_cal = fit_oof_calibrator(market, model, valid_train, all_feature_map)
                calibrators[market] = oof_cal
                logger.info(f"  [{market}] trained on {len(X):,} examples, oof_cal={'fit' if oof_cal else 'none'}")

        window_bets = simulate_bets(models, calibrators, test_fx, test_feature_map, STARTING_BANKROLL)

        for mode in CAL_MODES:
            bets_by_mode[mode].extend(window_bets[mode])

    # Build output
    output = {}
    for mode in CAL_MODES:
        bets = bets_by_mode[mode]
        roi, ci_lo, ci_hi = bootstrap_roi(bets)
        n_cands = len(odds_fixtures)
        pass_rate = len(bets) / n_cands if n_cands else 0

        by_market = market_summary(bets)

        output[mode] = {
            "n_bets": len(bets),
            "n_candidates": n_cands,
            "ev_pass_rate": round(pass_rate, 4),
            "roi_pct": round(roi * 100, 2),
            "ci_95": [round(ci_lo * 100, 2), round(ci_hi * 100, 2)],
            "avg_ev": round(np.mean([b["ev"] for b in bets]), 4) if bets else 0,
            "avg_p_final": round(np.mean([b["p_blended"] for b in bets]), 4) if bets else 0,
            "by_market": by_market,
        }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Results written to {OUTPUT_PATH}")

    # Print summary
    print("\n" + "=" * 72)
    print("WALK-FORWARD BACKTEST V3 SUMMARY (production formula + Shin blend)")
    print("=" * 72)
    header = f"{'Mode':<15} {'Bets':>6} {'Pass%':>7} {'ROI%':>8} {'95% CI':>22} {'AvgEV':>8}"
    print(header)
    print("-" * 72)
    for mode in CAL_MODES:
        r = output[mode]
        print(f"{mode:<15} {r['n_bets']:>6} {r['ev_pass_rate']*100:>6.1f}%"
              f" {r['roi_pct']:>7.1f}% [{r['ci_95'][0]:>6.1f}%, {r['ci_95'][1]:>6.1f}%]"
              f" {r['avg_ev']:>8.4f}")

    print()
    print("OU split (oof_blend mode):")
    for mkt in ["ou25", "ou15"]:
        d = output["oof_blend"]["by_market"].get(mkt, {})
        if d:
            print(f"  {mkt}: n={d['n_bets']}, win_rate={d['win_rate']:.3f}, "
                  f"avg_odds={d['avg_odds']:.3f}, roi={d['roi_pct']:.1f}%, "
                  f"ci=[{d['ci_95'][0]:.1f}%, {d['ci_95'][1]:.1f}%], avg_ev={d['avg_ev']:.4f}")

    print()
    print("Market breakdown (oof_blend mode):")
    for mkt in ["h2h", "btts", "ou25", "ou15"]:
        d = output["oof_blend"]["by_market"].get(mkt, {})
        if d:
            print(f"  {mkt}: n={d['n_bets']:5d}, win_rate={d['win_rate']:.3f}, "
                  f"avg_odds={d['avg_odds']:.3f}, roi={d['roi_pct']:.1f}%, "
                  f"ci=[{d['ci_95'][0]:.1f}%, {d['ci_95'][1]:.1f}%]")


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    run()
