#!/usr/bin/env python3
# ruff: noqa
import warnings
warnings.filterwarnings("ignore", message="X does not have valid feature names")
"""
Walk-forward backtest V2 — calibrated, heterogeneity-aware, ou25/ou15 split.

Extends V1 with:
  D.  Platt calibration (in-sample vs out-of-fold)
  E.  ou25 and ou15 separated
  G.  League heterogeneity metrics + cluster-stratified calibration

Calibration methods
  - raw:       uncalibrated LightGBM output (replicated from V1)
  - insample:  Platt calibrator fit on training-window predictions (optimistic upper bound)
  - outoffold: Platt calibrator fit on chronological 30% validation subset within
               training window (honest — never touches the test period)
  - clustered: same out-of-fold approach, but one calibrator per league cluster

Cluster features: avg_goals, btts_rate, HHI (concentration)
  Leagues with insufficient data use global cluster (cluster 0).

Usage:
    cd /opt/projects/bootball
    python3 scripts/analysis/walk_forward_backtest_v2.py

Output: scripts/analysis/backtest_results_v2.json
"""

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backtest_v2")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "football.db"
OUTPUT_PATH = Path(__file__).resolve().parent / "backtest_results_v2.json"

BOT_MIN_EV = 0.05
KELLY_FRACTION = 0.25
MIN_BET = 10.0
STARTING_BANKROLL = 1000.0
DEFAULT_RANK = 15
DEFAULT_GF = 1.0
DEFAULT_GA = 1.0
N_CLUSTERS = 3
CALIB_HOLDOUT_FRAC = 0.30   # fraction of training window kept for calibration fitting

WINDOW_STARTS = [datetime(2026, 4, 15), datetime(2026, 5, 1), datetime(2026, 6, 1)]
WINDOW_END = datetime(2026, 6, 17)


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
    cols = ["id","league_id","season","home_team_id","away_team_id","date","goals_home","goals_away","outcome"]
    conn.close()
    result = [dict(zip(cols, r)) for r in rows]
    logger.info(f"Loaded {len(result):,} training fixtures")
    return result


def load_odds_fixtures() -> List[dict]:
    """Load FT fixtures with odds — separate ou25 and ou15 columns."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT
            f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
            f.date, f.goals_home, f.goals_away, f.outcome,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_home END)       AS odd_home,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_draw END)       AS odd_draw,
            MAX(CASE WHEN fo.bet_type='h2h'  THEN fo.odd_away END)       AS odd_away,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_yes END)   AS odd_btts_yes,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_no END)    AS odd_btts_no,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over END) AS odd_ou25_over,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under END) AS odd_ou25_under,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over15 END) AS odd_ou15_over,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under15 END) AS odd_ou15_under
        FROM fixtures f
        JOIN fixture_odds fo ON fo.fixture_id = f.id
        WHERE f.season = 2025 AND f.status = 'FT'
          AND f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
          AND f.outcome IS NOT NULL
        GROUP BY f.id
    """).fetchall()
    cols = ["id","league_id","season","home_team_id","away_team_id","date","goals_home","goals_away","outcome",
            "odd_home","odd_draw","odd_away","odd_btts_yes","odd_btts_no",
            "odd_ou25_over","odd_ou25_under","odd_ou15_over","odd_ou15_under"]
    conn.close()
    result = [dict(zip(cols, r)) for r in rows]
    logger.info(f"Loaded {len(result):,} fixtures with odds (test candidates)")
    return result


# ── League heterogeneity metrics ──────────────────────────────────────────────

def compute_league_metrics(all_fixtures: List[dict]) -> Dict[int, dict]:
    """
    For each league_id in the fixture pool, compute:
      avg_goals, btts_rate, hhi (Herfindahl–Hirschman concentration on team wins)

    Returns {league_id: {"avg_goals": float, "btts_rate": float, "hhi": float}}
    """
    # Group by league
    by_league: Dict[int, List[dict]] = defaultdict(list)
    for f in all_fixtures:
        by_league[f["league_id"]].append(f)

    metrics = {}
    for lid, fixtures in by_league.items():
        if len(fixtures) < 10:
            continue
        total_goals = sum(f["goals_home"] + f["goals_away"] for f in fixtures)
        btts = sum(1 for f in fixtures if f["goals_home"] >= 1 and f["goals_away"] >= 1)
        avg_goals = total_goals / len(fixtures)
        btts_rate = btts / len(fixtures)

        # HHI on team wins
        win_counts: Dict[int, int] = defaultdict(int)
        for f in fixtures:
            if f["goals_home"] > f["goals_away"]:
                win_counts[f["home_team_id"]] += 1
            elif f["goals_away"] > f["goals_home"]:
                win_counts[f["away_team_id"]] += 1
        total_wins = sum(win_counts.values()) or 1
        hhi = sum((w / total_wins) ** 2 for w in win_counts.values())

        metrics[lid] = {"avg_goals": avg_goals, "btts_rate": btts_rate, "hhi": hhi}

    return metrics


def cluster_leagues(league_metrics: Dict[int, dict]) -> Tuple[Dict[int, int], dict]:
    """
    Cluster leagues into N_CLUSTERS groups using [avg_goals, btts_rate, hhi].
    Returns ({league_id: cluster_id}, cluster_summary)
    """
    if len(league_metrics) < N_CLUSTERS:
        # Not enough leagues — everyone gets cluster 0
        return {lid: 0 for lid in league_metrics}, {}

    lids = sorted(league_metrics.keys())
    X = np.array([[league_metrics[l]["avg_goals"],
                   league_metrics[l]["btts_rate"],
                   league_metrics[l]["hhi"]] for l in lids])

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    km.fit(X_scaled)
    labels = km.labels_

    assignment = {lid: int(labels[i]) for i, lid in enumerate(lids)}

    # Cluster summary
    summary = {}
    for c in range(N_CLUSTERS):
        members = [lids[i] for i, lb in enumerate(labels) if lb == c]
        vals = [league_metrics[l] for l in members]
        summary[c] = {
            "n_leagues": len(members),
            "avg_goals_mean": round(np.mean([v["avg_goals"] for v in vals]), 2),
            "btts_rate_mean": round(np.mean([v["btts_rate"] for v in vals]), 3),
            "hhi_mean": round(np.mean([v["hhi"] for v in vals]), 4),
        }

    return assignment, summary


# ── Point-in-time feature construction ───────────────────────────────────────

def build_team_history(fixtures: List[dict]) -> Dict:
    history: Dict = {}
    for f in fixtures:
        hid, aid, lid, date = f["home_team_id"], f["away_team_id"], f["league_id"], f["date"]
        hg, ag = f["goals_home"], f["goals_away"]
        h_pts = 3 if hg > ag else (1 if hg == ag else 0)
        a_pts = 3 if ag > hg else (1 if ag == hg else 0)
        for (tid, gf, ga, pts) in [(hid, hg, ag, h_pts), (aid, ag, hg, a_pts)]:
            key = (lid, tid)
            if key not in history:
                history[key] = []
            history[key].append((date, gf, ga, pts))
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


# ── Platt calibration helpers ─────────────────────────────────────────────────

def _logit(p: float) -> float:
    p = max(1e-7, min(1 - 1e-7, float(p)))
    return np.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def fit_platt(probs: np.ndarray, labels: np.ndarray) -> Optional[LogisticRegression]:
    """Fit a binary Platt calibrator: logistic regression on logit(p) → actual outcome."""
    if len(probs) < 10 or len(np.unique(labels)) < 2:
        return None
    X_cal = np.array([[_logit(p)] for p in probs])
    lr = LogisticRegression(solver="lbfgs", max_iter=1000)
    lr.fit(X_cal, labels)
    return lr


def apply_platt(calibrator: Optional[LogisticRegression], p: float) -> float:
    if calibrator is None:
        return p
    try:
        x = np.array([[_logit(p)]])
        # predict_proba returns [[p_class0, p_class1]]; want P(class=1)
        return float(calibrator.predict_proba(x)[0][1])
    except Exception:
        return p


def get_best_outcome_probs_h2h(model, X_single, train_feats, train_labels):
    """
    For h2h: predict all 3 class probs, return list of (outcome_char, raw_prob) tuples.
    """
    probs = model.predict_proba(X_single)[0]   # [p_H, p_D, p_A]
    return [("H", probs[0]), ("D", probs[1]), ("A", probs[2])]


# ── EV / Kelly ────────────────────────────────────────────────────────────────

def compute_ev(p, odds):
    return p * odds - (1.0 - p)

def compute_kelly(p, odds):
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - (1 - p)) / b) * KELLY_FRACTION


# ── Per-window calibration fitting ────────────────────────────────────────────

def build_calibration_data(model, market, train_fixtures, feature_map, split_frac=CALIB_HOLDOUT_FRAC):
    """
    Vectorized batch calibration data builder.
    Chronological split: last split_frac portion is the calibration set.
    Returns (calib_probs, calib_labels, calib_league_ids).

    For h2h: probability of the argmax class (selected outcome).
    For binary markets: probability of the positive class (1).
    """
    if model is None or not train_fixtures:
        return np.array([]), np.array([]), []

    n = len(train_fixtures)
    split_idx = int(n * (1 - split_frac))
    calib_fixtures = train_fixtures[split_idx:]

    # Collect valid fixtures and build batch feature matrix
    valid_fixtures = [f for f in calib_fixtures if feature_map.get(f["id"]) is not None]
    if not valid_fixtures:
        return np.array([]), np.array([]), []

    X_batch = np.array([feature_map[f["id"]] for f in valid_fixtures])
    all_raw_probs = model.predict_proba(X_batch)   # shape (n, num_classes)

    probs_list, labels_list, league_ids = [], [], []
    for i, f in enumerate(valid_fixtures):
        raw_probs = all_raw_probs[i]
        if market == "h2h":
            best_idx = int(np.argmax(raw_probs))
            p = raw_probs[best_idx]
            label = 1 if h2h_label(f) == best_idx else 0
        elif market == "btts":
            p = raw_probs[1]
            label = btts_label(f)
        elif market == "ou25":
            p = raw_probs[1]
            label = ou25_label(f)
        elif market == "ou15":
            p = raw_probs[1]
            label = ou15_label(f)
        else:
            continue
        probs_list.append(p)
        labels_list.append(label)
        league_ids.append(f["league_id"])

    return np.array(probs_list), np.array(labels_list), league_ids


def fit_calibrators(market, model, train_fixtures, feature_map,
                    league_cluster_map, n_clusters):
    """
    Returns:
        calibrators: {
            "insample": LogisticRegression (fit on ALL training predictions, biased),
            "outoffold": LogisticRegression (fit on calib holdout, honest),
            "clustered": {cluster_id: LogisticRegression}  (one per cluster, honest)
        }
    """
    if model is None:
        return {"insample": None, "outoffold": None, "clustered": {}}

    # --- In-sample calibration: fit on full training set predictions ---
    all_probs, all_labels, all_lids = build_calibration_data(
        model, market, train_fixtures, feature_map, split_frac=1.0)
    insample_cal = fit_platt(all_probs, all_labels) if len(all_probs) >= 20 else None

    # --- Out-of-fold calibration: chronological last 30% of training window ---
    oof_probs, oof_labels, oof_lids = build_calibration_data(
        model, market, train_fixtures, feature_map, split_frac=CALIB_HOLDOUT_FRAC)
    outoffold_cal = fit_platt(oof_probs, oof_labels) if len(oof_probs) >= 20 else None

    # --- Cluster-stratified calibration: one calibrator per cluster ---
    clustered_cals = {}
    if len(oof_probs) >= 10:
        cluster_probs: Dict[int, list] = defaultdict(list)
        cluster_labels: Dict[int, list] = defaultdict(list)
        for p, lbl, lid in zip(oof_probs, oof_labels, oof_lids):
            c = league_cluster_map.get(lid, 0)
            cluster_probs[c].append(p)
            cluster_labels[c].append(lbl)
        for c in range(n_clusters):
            if c in cluster_probs and len(cluster_probs[c]) >= 10:
                clustered_cals[c] = fit_platt(np.array(cluster_probs[c]),
                                               np.array(cluster_labels[c]))

    return {"insample": insample_cal, "outoffold": outoffold_cal, "clustered": clustered_cals}


# ── Betting simulation ─────────────────────────────────────────────────────────

def _record_bet(fid, date, market, outcome, our_prob_raw, cal_type, calibrated_prob, odds,
                ev, kelly, bankroll, actual_fn, actual_outcome_str):
    if ev < BOT_MIN_EV:
        return None
    stake = compute_kelly(calibrated_prob, odds) * bankroll
    if stake < MIN_BET:
        return None
    won = actual_fn()
    pnl = stake * (odds - 1) if won else -stake
    return {
        "fixture_id": fid,
        "date": date,
        "market": market,
        "outcome": outcome,
        "cal_type": cal_type,
        "our_prob_raw": round(our_prob_raw, 4),
        "our_prob_cal": round(calibrated_prob, 4),
        "odds": odds,
        "ev": round(ev, 4),
        "stake": round(stake, 2),
        "won": won,
        "pnl": round(pnl, 2),
        "actual_outcome": actual_outcome_str,
    }


def simulate_bets(
    models,          # {"h2h": LGBMClassifier, "btts": ..., "ou25": ..., "ou15": ...}
    calibrators,     # {"h2h": {"insample": LR, "outoffold": LR, "clustered": {...}}, ...}
    test_fixtures,
    feature_map,
    league_cluster_map,
    bankroll,
):
    """Run all 4 calibration modes and collect bets. Returns {cal_type: [bet_dict, ...]}"""
    all_bets: Dict[str, List[dict]] = {t: [] for t in ["raw", "insample", "outoffold", "clustered"]}

    for f in test_fixtures:
        fid = f["id"]
        date = f["date"]
        lid = f["league_id"]
        cluster = league_cluster_map.get(lid, 0)
        feats = feature_map.get(fid)
        if feats is None:
            continue
        X = feats.reshape(1, -1)

        for market, odds_pairs, label_fn, actual_str_fn in [
            ("h2h",
             [("H", f.get("odd_home")), ("D", f.get("odd_draw")), ("A", f.get("odd_away"))],
             None,
             lambda f=f: f["outcome"]),
            ("btts",
             [("Yes", f.get("odd_btts_yes")), ("No", f.get("odd_btts_no"))],
             None,
             lambda f=f: "Yes" if btts_label(f) else "No"),
            ("ou25",
             [("Over", f.get("odd_ou25_over")), ("Under", f.get("odd_ou25_under"))],
             None,
             lambda f=f: "Over" if ou25_label(f) else "Under"),
            ("ou15",
             [("Over", f.get("odd_ou15_over")), ("Under", f.get("odd_ou15_under"))],
             None,
             lambda f=f: "Over" if ou15_label(f) else "Under"),
        ]:
            model = models.get(market)
            if model is None:
                continue
            cals = calibrators.get(market, {})

            try:
                raw_probs = model.predict_proba(X)[0]
            except Exception:
                continue

            # Map outcome label → raw probability
            if market == "h2h":
                label_to_idx = {"H": 0, "D": 1, "A": 2}
                actual_outcome_str = actual_str_fn()
                def won_fn(outcome_label, f=f):
                    return f["outcome"] == outcome_label
            elif market == "btts":
                # raw_probs = [p_no, p_yes]
                label_to_prob = {"Yes": raw_probs[1], "No": raw_probs[0]}
                actual_outcome_str = actual_str_fn()
                def won_fn(outcome_label, f=f):
                    return ("Yes" if btts_label(f) else "No") == outcome_label
            elif market in ("ou25", "ou15"):
                label_to_prob = {"Over": raw_probs[1], "Under": raw_probs[0]}
                actual_outcome_str = actual_str_fn()
                lbl_fn = ou25_label if market == "ou25" else ou15_label
                def won_fn(outcome_label, f=f, lf=lbl_fn):
                    return ("Over" if lf(f) else "Under") == outcome_label

            # Pick best EV outcome for each calibration type
            for cal_type in ["raw", "insample", "outoffold", "clustered"]:
                if cal_type == "raw":
                    calibrator = None
                elif cal_type == "insample":
                    calibrator = cals.get("insample")
                elif cal_type == "outoffold":
                    calibrator = cals.get("outoffold")
                elif cal_type == "clustered":
                    clustered = cals.get("clustered", {})
                    calibrator = clustered.get(cluster) or cals.get("outoffold")

                # Compute calibrated probs for all outcomes
                best_ev = -999.0
                best_bet = None

                for outcome_label, odds in odds_pairs:
                    if not odds or odds < 1.0:
                        continue

                    if market == "h2h":
                        raw_p = raw_probs[label_to_idx[outcome_label]]
                        # For h2h calibration: apply calibrator to the probability
                        # of the specific outcome being predicted
                        cal_p = apply_platt(calibrator, raw_p)
                        actual_correct = f["outcome"] == outcome_label
                    else:
                        raw_p = label_to_prob[outcome_label]
                        cal_p = apply_platt(calibrator, raw_p)
                        if market == "btts":
                            actual_correct = ("Yes" if btts_label(f) else "No") == outcome_label
                        elif market == "ou25":
                            actual_correct = ("Over" if ou25_label(f) else "Under") == outcome_label
                        else:
                            actual_correct = ("Over" if ou15_label(f) else "Under") == outcome_label

                    ev = compute_ev(cal_p, odds)
                    if ev > best_ev:
                        best_ev = ev
                        best_bet = (outcome_label, raw_p, cal_p, odds, actual_correct)

                if best_bet is None or best_ev < BOT_MIN_EV:
                    continue
                outcome_label, raw_p, cal_p, odds, actual_correct = best_bet
                stake = compute_kelly(cal_p, odds) * bankroll
                if stake < MIN_BET:
                    continue
                pnl = stake * (odds - 1) if actual_correct else -stake
                all_bets[cal_type].append({
                    "fixture_id": fid,
                    "date": date,
                    "market": market,
                    "league_id": lid,
                    "cluster": cluster,
                    "outcome": outcome_label,
                    "cal_type": cal_type,
                    "our_prob_raw": round(raw_p, 4),
                    "our_prob_cal": round(cal_p, 4),
                    "odds": odds,
                    "ev": round(best_ev, 4),
                    "stake": round(stake, 2),
                    "won": actual_correct,
                    "pnl": round(pnl, 2),
                    "actual_outcome": actual_outcome_str,
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


def calibration_deciles(bets, prob_field="our_prob_cal"):
    if len(bets) < 20:
        return []
    sb = sorted(bets, key=lambda b: b[prob_field])
    n = len(sb)
    size = max(1, n // 10)
    out = []
    for i in range(10):
        chunk = sb[i * size: (i + 1) * size if i < 9 else n]
        if not chunk:
            continue
        out.append({
            "decile": i + 1,
            "n": len(chunk),
            "mean_predicted": round(np.mean([b[prob_field] for b in chunk]), 4),
            "actual_win_rate": round(np.mean([1.0 if b["won"] else 0.0 for b in chunk]), 4),
        })
    return out


def market_summary(bets):
    if not bets:
        return {}
    markets = sorted(set(b["market"] for b in bets))
    result = {}
    for m in markets:
        mb = [b for b in bets if b["market"] == m]
        roi, ci_lo, ci_hi = bootstrap_roi(mb)
        n_total = len(mb)
        n_candidates = n_total  # all are post-filter
        result[m] = {
            "n_bets": n_total,
            "staked": round(sum(b["stake"] for b in mb), 2),
            "pnl": round(sum(b["pnl"] for b in mb), 2),
            "roi_pct": round(roi * 100, 2),
            "ci_95": [round(ci_lo * 100, 2), round(ci_hi * 100, 2)],
            "win_rate": round(np.mean([1 if b["won"] else 0 for b in mb]), 4),
            "avg_odds": round(np.mean([b["odds"] for b in mb]), 3),
            "calibration_deciles": calibration_deciles(mb),
        }
    return result


# ── Main walk-forward loop ────────────────────────────────────────────────────

def run():
    all_fixtures = load_all_fixtures()
    odds_fixtures = load_odds_fixtures()

    # Build feature infrastructure
    history = build_team_history(all_fixtures)
    league_teams: Dict[int, List[int]] = defaultdict(set)
    for f in all_fixtures:
        league_teams[f["league_id"]].add(f["home_team_id"])
        league_teams[f["league_id"]].add(f["away_team_id"])
    league_teams = {lid: list(tids) for lid, tids in league_teams.items()}

    # League heterogeneity + clustering
    league_metrics = compute_league_metrics(all_fixtures)
    league_cluster_map, cluster_summary = cluster_leagues(league_metrics)
    logger.info(f"League clusters: {cluster_summary}")

    # Pre-build feature maps for all fixtures
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

    # Index test fixtures by date
    def parse_date(d):
        return datetime.fromisoformat(d[:19]) if d else datetime.min

    odds_fixtures_sorted = sorted(odds_fixtures, key=lambda f: f["date"])

    all_window_results = []
    bets_by_cal: Dict[str, List[dict]] = {t: [] for t in ["raw", "insample", "outoffold", "clustered"]}

    for w_idx, window_start in enumerate(WINDOW_STARTS):
        if w_idx + 1 < len(WINDOW_STARTS):
            window_end = WINDOW_STARTS[w_idx + 1]
        else:
            window_end = WINDOW_END

        cutoff = window_start.isoformat()
        window_start_str = window_start.isoformat()
        window_end_str = window_end.isoformat()

        # Training data: all FT fixtures strictly before cutoff
        train_fx = [f for f in all_fixtures if f["date"] < cutoff]
        # Test data: odds-bearing fixtures in [window_start, window_end)
        test_fx = [f for f in odds_fixtures_sorted
                   if window_start_str <= f["date"] < window_end_str]

        logger.info(f"Window {w_idx+1}: train={len(train_fx):,}, test={len(test_fx):,}")

        if not train_fx or not test_fx:
            continue

        # Build per-market training sets
        markets_config = {
            "h2h": (h2h_label, lambda y: [yi for yi in y if yi >= 0]),
            "btts": (btts_label, None),
            "ou25": (ou25_label, None),
            "ou15": (ou15_label, None),
        }

        models = {}
        for market, (label_fn, _) in markets_config.items():
            X_list, y_list = [], []
            for f in train_fx:
                fid = f["id"]
                feats = all_feature_map.get(fid)
                if feats is None:
                    continue
                lbl = label_fn(f)
                if market == "h2h" and lbl < 0:
                    continue
                X_list.append(feats)
                y_list.append(lbl)
            if X_list:
                X_train = np.array(X_list)
                y_train = np.array(y_list)
                models[market] = fit_lgbm(market, X_train, y_train)
                logger.info(f"  [{market}] trained on {len(X_train):,} examples")

        # Fit calibrators for each market
        calibrators = {}
        for market in ["h2h", "btts", "ou25", "ou15"]:
            calibrators[market] = fit_calibrators(
                market, models.get(market), train_fx, all_feature_map,
                league_cluster_map, N_CLUSTERS
            )
            ins = calibrators[market]["insample"]
            oof = calibrators[market]["outoffold"]
            logger.info(f"  [{market}] insample_cal={'fit' if ins else 'none'}, "
                        f"outoffold_cal={'fit' if oof else 'none'}, "
                        f"clustered_cals={len(calibrators[market]['clustered'])}")

        # Simulate bets for this window
        window_bets = simulate_bets(
            models, calibrators, test_fx, odds_feature_map,
            league_cluster_map, STARTING_BANKROLL
        )

        # Collect pass rates
        n_test = len(test_fx)
        pass_rates = {cal: len(bets) / max(1, n_test)
                      for cal, bets in window_bets.items()}

        window_result = {
            "window": w_idx + 1,
            "cutoff": cutoff,
            "train_size": len(train_fx),
            "test_size": n_test,
            "pass_rates": {k: round(v, 4) for k, v in pass_rates.items()},
            "bets_by_cal": {cal: len(bets) for cal, bets in window_bets.items()},
        }
        all_window_results.append(window_result)

        for cal_type, bets in window_bets.items():
            bets_by_cal[cal_type].extend(bets)

    # Overall summary by calibration type and market
    logger.info("Computing summary statistics...")
    results = {
        "generated_at": datetime.utcnow().isoformat(),
        "league_heterogeneity": {
            "n_leagues_with_metrics": len(league_metrics),
            "metric_distributions": {
                "avg_goals": {
                    "min": round(min(v["avg_goals"] for v in league_metrics.values()), 2),
                    "p25": round(float(np.percentile([v["avg_goals"] for v in league_metrics.values()], 25)), 2),
                    "median": round(float(np.median([v["avg_goals"] for v in league_metrics.values()])), 2),
                    "p75": round(float(np.percentile([v["avg_goals"] for v in league_metrics.values()], 75)), 2),
                    "max": round(max(v["avg_goals"] for v in league_metrics.values()), 2),
                },
                "btts_rate": {
                    "min": round(min(v["btts_rate"] for v in league_metrics.values()), 3),
                    "p25": round(float(np.percentile([v["btts_rate"] for v in league_metrics.values()], 25)), 3),
                    "median": round(float(np.median([v["btts_rate"] for v in league_metrics.values()])), 3),
                    "p75": round(float(np.percentile([v["btts_rate"] for v in league_metrics.values()], 75)), 3),
                    "max": round(max(v["btts_rate"] for v in league_metrics.values()), 3),
                },
                "hhi": {
                    "min": round(min(v["hhi"] for v in league_metrics.values()), 4),
                    "p25": round(float(np.percentile([v["hhi"] for v in league_metrics.values()], 25)), 4),
                    "median": round(float(np.median([v["hhi"] for v in league_metrics.values()])), 4),
                    "p75": round(float(np.percentile([v["hhi"] for v in league_metrics.values()], 75)), 4),
                    "max": round(max(v["hhi"] for v in league_metrics.values()), 4),
                },
            },
            "clusters": cluster_summary,
        },
        "walk_forward_windows": all_window_results,
        "by_cal_type": {},
    }

    cal_labels = {
        "raw": "Uncalibrated (V1 baseline)",
        "insample": "In-sample Platt (optimistic upper bound)",
        "outoffold": "Out-of-fold Platt (honest — matches production intent)",
        "clustered": "Cluster-stratified Platt (one calibrator per league cluster)",
    }

    for cal_type in ["raw", "insample", "outoffold", "clustered"]:
        bets = bets_by_cal[cal_type]
        if not bets:
            results["by_cal_type"][cal_type] = {"description": cal_labels[cal_type], "n_bets": 0}
            continue

        n_total = sum(len(b) for b in bets_by_cal.values()) // 4 or 1
        roi, ci_lo, ci_hi = bootstrap_roi(bets)

        # EV pass rate vs total test candidates
        total_candidates = sum(r["test_size"] * 4 for r in all_window_results)  # 4 markets
        pass_rate = len(bets) / total_candidates if total_candidates > 0 else 0.0

        results["by_cal_type"][cal_type] = {
            "description": cal_labels[cal_type],
            "n_bets": len(bets),
            "ev_pass_rate": round(pass_rate, 4),
            "staked": round(sum(b["stake"] for b in bets), 2),
            "pnl": round(sum(b["pnl"] for b in bets), 2),
            "roi_pct": round(roi * 100, 2),
            "ci_95_pct": [round(ci_lo * 100, 2), round(ci_hi * 100, 2)],
            "win_rate": round(float(np.mean([1 if b["won"] else 0 for b in bets])), 4),
            "avg_ev": round(float(np.mean([b["ev"] for b in bets])), 4),
            "by_market": market_summary(bets),
        }

    # Compute raw EV pass rate from first window's metrics
    raw_pass = bets_by_cal["raw"]
    outoffold_pass = bets_by_cal["outoffold"]
    results["ev_filter_comparison"] = {
        "raw_pass_count": len(raw_pass),
        "outoffold_pass_count": len(outoffold_pass),
        "raw_avg_ev": round(float(np.mean([b["ev"] for b in raw_pass])), 4) if raw_pass else 0,
        "outoffold_avg_ev": round(float(np.mean([b["ev"] for b in outoffold_pass])), 4) if outoffold_pass else 0,
        "filter_selectivity_ratio": round(len(outoffold_pass) / max(1, len(raw_pass)), 4),
    }

    # Save
    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    logger.info(f"Results written to {OUTPUT_PATH}")

    # Print summary
    print("\n" + "=" * 70)
    print("WALK-FORWARD BACKTEST V2 SUMMARY")
    print("=" * 70)

    print(f"\nLeague heterogeneity: {len(league_metrics)} leagues measured")
    if cluster_summary:
        for c, s in sorted(cluster_summary.items()):
            print(f"  Cluster {c}: {s['n_leagues']} leagues, avg_goals={s['avg_goals_mean']}, "
                  f"btts={s['btts_rate_mean']:.3f}, HHI={s['hhi_mean']:.4f}")

    print(f"\n{'Cal type':<12} {'Bets':>6} {'Pass%':>7} {'ROI%':>7} {'95% CI':>20} {'Avg EV':>8}")
    print("-" * 70)
    for cal_type in ["raw", "insample", "outoffold", "clustered"]:
        d = results["by_cal_type"].get(cal_type, {})
        if not d.get("n_bets"):
            continue
        ci = d.get("ci_95_pct", [0, 0])
        print(f"{cal_type:<12} {d['n_bets']:>6} {d['ev_pass_rate']*100:>6.1f}% "
              f"{d['roi_pct']:>7.1f}% [{ci[0]:>6.1f}%, {ci[1]:>6.1f}%] "
              f"{d['avg_ev']:>8.4f}")

    print(f"\nOU split comparison (outoffold):")
    outoffold = results["by_cal_type"].get("outoffold", {}).get("by_market", {})
    for m in ["ou25", "ou15"]:
        if m in outoffold:
            d = outoffold[m]
            ci = d.get("ci_95", [0, 0])
            print(f"  {m}: n={d['n_bets']}, roi={d['roi_pct']:.1f}%, ci=[{ci[0]:.1f}%, {ci[1]:.1f}%]")

    return results


if __name__ == "__main__":
    run()
