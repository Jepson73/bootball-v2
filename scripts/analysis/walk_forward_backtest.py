#!/usr/bin/env python3
"""
Walk-forward backtest harness for Bootball V1 out-of-sample validation.

Design principles:
- Point-in-time features: standings are RECONSTRUCTED from the fixtures table
  using only matches played BEFORE each fixture's date. The standings table
  holds final/live standings and must NOT be used (it leaks future results).
- Exact 9-feature set from production trainer.py / prediction.py
- Model family: LightGBMClassifier (matches deployed .pkl files)
- EV threshold: 5% (bot_min_ev = 0.05), Kelly fraction: 0.25, min_bet: £10
- No writes to production DB; results go to scripts/analysis/backtest_results.json

Usage:
    cd /opt/projects/bootball
    python3 scripts/analysis/walk_forward_backtest.py

Output: scripts/analysis/backtest_results.json
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import log_loss

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backtest")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "football.db"
OUTPUT_PATH = Path(__file__).resolve().parent / "backtest_results.json"

# Production constants
BOT_MIN_EV = 0.05      # 5% EV threshold from markowitz_optimizer.py
KELLY_FRACTION = 0.25  # from unified_prediction_service.py line 142
MIN_BET = 10.0         # from markowitz_optimizer.py
STARTING_BANKROLL = 1000.0

# Production default fallbacks when team has no prior history in the DB
DEFAULT_RANK = 15
DEFAULT_GF = 1.0
DEFAULT_GA = 1.0

# Walk-forward windows: test on each calendar month in the odds window (Apr-Jun 2026)
WINDOW_STARTS = [
    datetime(2026, 4, 15),
    datetime(2026, 5, 1),
    datetime(2026, 6, 1),
]
WINDOW_END = datetime(2026, 6, 17)

# ── Data loading ──────────────────────────────────────────────────────────────

def load_all_fixtures() -> List[dict]:
    """Load all 2025-season FT fixtures with goals — the feature/label pool."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, league_id, season, home_team_id, away_team_id,
               date, goals_home, goals_away, outcome
        FROM fixtures
        WHERE season = 2025
          AND status = 'FT'
          AND goals_home IS NOT NULL
          AND goals_away IS NOT NULL
          AND outcome IS NOT NULL
        ORDER BY date ASC
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    logger.info(f"Loaded {len(rows):,} 2025-season FT fixtures (feature/label pool)")
    return rows


def load_odds_fixtures() -> List[dict]:
    """Load fixtures that have pre-match odds stored — the test candidates."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
            f.date, f.goals_home, f.goals_away, f.outcome,
            MAX(CASE WHEN fo.bet_type='h2h' THEN fo.odd_home END)    AS odd_home,
            MAX(CASE WHEN fo.bet_type='h2h' THEN fo.odd_draw END)    AS odd_draw,
            MAX(CASE WHEN fo.bet_type='h2h' THEN fo.odd_away END)    AS odd_away,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_yes END) AS odd_btts_yes,
            MAX(CASE WHEN fo.bet_type='btts' THEN fo.odd_btts_no END)  AS odd_btts_no,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_over END)  AS odd_over,
            MAX(CASE WHEN fo.bet_type='over_under' THEN fo.odd_under END) AS odd_under
        FROM fixtures f
        JOIN fixture_odds fo ON fo.fixture_id = f.id
        WHERE f.season = 2025
          AND f.status = 'FT'
          AND f.goals_home IS NOT NULL
          AND f.goals_away IS NOT NULL
          AND f.outcome IS NOT NULL
        GROUP BY f.id
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    logger.info(f"Loaded {len(rows):,} fixtures with odds (test candidate pool)")
    return rows


# ── Point-in-time feature construction ───────────────────────────────────────

def build_team_history(all_fixtures: List[dict]) -> Dict:
    """
    Precompute a per-team, per-league match history sorted by date.

    Returns:
        {(league_id, team_id): [(date_str, gf, ga, points), ...]}  sorted ascending by date

    This is used to look up each team's cumulative stats BEFORE a given date
    without touching the standings table.
    """
    history: Dict[Tuple, List] = {}

    for f in all_fixtures:
        hid = f["home_team_id"]
        aid = f["away_team_id"]
        lid = f["league_id"]
        date = f["date"]
        hg = f["goals_home"]
        ag = f["goals_away"]

        # Home team contribution
        h_pts = 3 if hg > ag else (1 if hg == ag else 0)
        key_h = (lid, hid)
        if key_h not in history:
            history[key_h] = []
        history[key_h].append((date, hg, ag, h_pts))

        # Away team contribution
        a_pts = 3 if ag > hg else (1 if ag == hg else 0)
        key_a = (lid, aid)
        if key_a not in history:
            history[key_a] = []
        history[key_a].append((date, ag, hg, a_pts))

    # Sort each entry by date ascending (already ordered, but ensure)
    for key in history:
        history[key].sort(key=lambda x: x[0])

    return history


def get_team_stats_before(history: Dict, league_id: int, team_id: int, date_str: str) -> Tuple[float, float, float]:
    """
    Return (gf, ga, points) for team in league using only matches played before date_str.
    Falls back to production defaults if no history.
    """
    key = (league_id, team_id)
    entries = history.get(key, [])

    cum_gf = 0.0
    cum_ga = 0.0
    cum_pts = 0.0
    for (d, gf, ga, pts) in entries:
        if d >= date_str:  # strict less-than — exclude same date and future
            break
        cum_gf += gf
        cum_ga += ga
        cum_pts += pts

    return cum_gf or DEFAULT_GF, cum_ga or DEFAULT_GA, cum_pts


def compute_rank(history: Dict, league_id: int, team_id: int, date_str: str, all_team_ids: List[int]) -> int:
    """
    Approximate rank for team_id in league_id as of date_str.

    Rank = 1 + number of teams with MORE points at that date.
    Uses only the set of teams that appear in the league in the provided all_fixtures list.
    Falls back to DEFAULT_RANK if team has zero history and very few others do too.
    """
    target_pts = get_team_stats_before(history, league_id, team_id, date_str)[2]

    rank = 1
    for other_id in all_team_ids:
        if other_id == team_id:
            continue
        other_pts = get_team_stats_before(history, league_id, other_id, date_str)[2]
        if other_pts > target_pts:
            rank += 1

    return rank


def build_features_for_fixture(
    f: dict,
    history: Dict,
    league_teams: Dict[int, List[int]],
) -> Optional[np.ndarray]:
    """
    Build the 9-feature vector for a fixture using point-in-time standings.

    Feature order (from trainer.py _build_features_h2h / _build_features_ou):
        [h_rank, a_rank, h_gf-h_ga, a_gf-a_ga, h_gf, a_gf, h_ga, a_ga, |h_rank-a_rank|]

    Same vector is used for h2h, btts, and over_under (as in production).
    """
    lid = f["league_id"]
    date = f["date"]
    hid = f["home_team_id"]
    aid = f["away_team_id"]

    all_teams_in_league = league_teams.get(lid, [])

    h_gf, h_ga, _ = get_team_stats_before(history, lid, hid, date)
    a_gf, a_ga, _ = get_team_stats_before(history, lid, aid, date)

    h_rank = compute_rank(history, lid, hid, date, all_teams_in_league)
    a_rank = compute_rank(history, lid, aid, date, all_teams_in_league)

    return np.array([
        h_rank,
        a_rank,
        h_gf - h_ga,
        a_gf - a_ga,
        h_gf,
        a_gf,
        h_ga,
        a_ga,
        abs(h_rank - a_rank),
    ], dtype=float)


# ── Label encoding ────────────────────────────────────────────────────────────

def h2h_label(f: dict) -> int:
    """H=0, D=1, A=2 — consistent with LightGBM num_class=3."""
    return {"H": 0, "D": 1, "A": 2}.get(f["outcome"], -1)


def btts_label(f: dict) -> int:
    """1 if both teams scored, else 0."""
    return 1 if (f["goals_home"] >= 1 and f["goals_away"] >= 1) else 0


def ou_label(f: dict) -> int:
    """1 if total goals > 2.5 (over), else 0 (under)."""
    return 1 if (f["goals_home"] + f["goals_away"]) > 2.5 else 0


# ── EV and Kelly ─────────────────────────────────────────────────────────────

def compute_kelly(our_prob: float, odds: float) -> float:
    """Kelly formula as used in unified_prediction_service.py lines 139-142."""
    b = odds - 1
    if b <= 0:
        return 0.0
    q = 1.0 - our_prob
    kelly = max(0.0, (b * our_prob - q) / b)
    return kelly * KELLY_FRACTION


def compute_ev(our_prob: float, odds: float) -> float:
    """EV formula from unified_prediction_service.py line 137."""
    return (our_prob * odds) - (1.0 - our_prob)


# ── Walk-forward engine ───────────────────────────────────────────────────────

def fit_model(market: str, X_train: np.ndarray, y_train: np.ndarray) -> Optional[lgb.LGBMClassifier]:
    """Fit a LightGBM model for the given market."""
    if len(X_train) < 50:
        logger.warning(f"[{market}] Only {len(X_train)} training samples — skipping")
        return None

    if market == "h2h":
        params = dict(
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            objective="multiclass",
            num_class=3,
            n_jobs=2,
            verbose=-1,
            random_state=42,
        )
    else:
        params = dict(
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            objective="binary",
            n_jobs=2,
            verbose=-1,
            random_state=42,
        )

    model = lgb.LGBMClassifier(**params)
    model.fit(X_train, y_train)
    return model


def simulate_bets_for_window(
    model_h2h, model_btts, model_ou,
    test_fixtures: List[dict],
    feature_map: Dict[int, np.ndarray],
    bankroll: float,
) -> List[dict]:
    """
    For each fixture in the test window:
      - Predict probabilities with each model
      - For each market, check if best-EV outcome clears the 5% threshold
      - Apply Kelly sizing; skip if stake < MIN_BET
      - Record outcome (won/lost/void)

    Returns list of bet records.
    """
    bets = []

    for f in test_fixtures:
        fid = f["id"]
        feats = feature_map.get(fid)
        if feats is None:
            continue

        X = feats.reshape(1, -1)

        # ── H2H market ───────────────────────────────────────────────────────
        if model_h2h is not None:
            try:
                probs_h2h = model_h2h.predict_proba(X)[0]  # [pH, pD, pA]
                outcomes_h2h = [
                    ("H", probs_h2h[0], f.get("odd_home")),
                    ("D", probs_h2h[1], f.get("odd_draw")),
                    ("A", probs_h2h[2], f.get("odd_away")),
                ]
                # Pick best EV outcome
                best = max(
                    [(o, p, od) for o, p, od in outcomes_h2h if od and od > 1.0],
                    key=lambda x: compute_ev(x[1], x[2]),
                    default=None,
                )
                if best:
                    outcome_label, our_prob, odds = best
                    ev = compute_ev(our_prob, odds)
                    if ev >= BOT_MIN_EV:
                        kelly = compute_kelly(our_prob, odds)
                        stake = kelly * bankroll
                        if stake >= MIN_BET:
                            won = (f["outcome"] == outcome_label)
                            pnl = stake * (odds - 1) if won else -stake
                            bets.append({
                                "fixture_id": fid,
                                "date": f["date"],
                                "market": "h2h",
                                "outcome": outcome_label,
                                "our_prob": round(our_prob, 4),
                                "odds": odds,
                                "ev": round(ev, 4),
                                "kelly": round(kelly, 4),
                                "stake": round(stake, 2),
                                "won": won,
                                "pnl": round(pnl, 2),
                                "actual_outcome": f["outcome"],
                            })
            except Exception as e:
                logger.debug(f"H2H prediction error for {fid}: {e}")

        # ── BTTS market ──────────────────────────────────────────────────────
        if model_btts is not None:
            try:
                probs_btts = model_btts.predict_proba(X)[0]  # [p_no, p_yes]
                btts_candidates = []
                if f.get("odd_btts_yes") and f["odd_btts_yes"] > 1.0:
                    btts_candidates.append(("Yes", probs_btts[1], f["odd_btts_yes"]))
                if f.get("odd_btts_no") and f["odd_btts_no"] > 1.0:
                    btts_candidates.append(("No", probs_btts[0], f["odd_btts_no"]))
                if btts_candidates:
                    best = max(btts_candidates, key=lambda x: compute_ev(x[1], x[2]))
                    outcome_label, our_prob, odds = best
                    ev = compute_ev(our_prob, odds)
                    if ev >= BOT_MIN_EV:
                        kelly = compute_kelly(our_prob, odds)
                        stake = kelly * bankroll
                        if stake >= MIN_BET:
                            actual_btts = "Yes" if btts_label(f) == 1 else "No"
                            won = (outcome_label == actual_btts)
                            pnl = stake * (odds - 1) if won else -stake
                            bets.append({
                                "fixture_id": fid,
                                "date": f["date"],
                                "market": "btts",
                                "outcome": outcome_label,
                                "our_prob": round(our_prob, 4),
                                "odds": odds,
                                "ev": round(ev, 4),
                                "kelly": round(kelly, 4),
                                "stake": round(stake, 2),
                                "won": won,
                                "pnl": round(pnl, 2),
                                "actual_outcome": actual_btts,
                            })
            except Exception as e:
                logger.debug(f"BTTS prediction error for {fid}: {e}")

        # ── Over/Under market ────────────────────────────────────────────────
        if model_ou is not None:
            try:
                probs_ou = model_ou.predict_proba(X)[0]  # [p_under, p_over]
                ou_candidates = []
                if f.get("odd_over") and f["odd_over"] > 1.0:
                    ou_candidates.append(("Over", probs_ou[1], f["odd_over"]))
                if f.get("odd_under") and f["odd_under"] > 1.0:
                    ou_candidates.append(("Under", probs_ou[0], f["odd_under"]))
                if ou_candidates:
                    best = max(ou_candidates, key=lambda x: compute_ev(x[1], x[2]))
                    outcome_label, our_prob, odds = best
                    ev = compute_ev(our_prob, odds)
                    if ev >= BOT_MIN_EV:
                        kelly = compute_kelly(our_prob, odds)
                        stake = kelly * bankroll
                        if stake >= MIN_BET:
                            actual_ou = "Over" if ou_label(f) == 1 else "Under"
                            won = (outcome_label == actual_ou)
                            pnl = stake * (odds - 1) if won else -stake
                            bets.append({
                                "fixture_id": fid,
                                "date": f["date"],
                                "market": "over_under",
                                "outcome": outcome_label,
                                "our_prob": round(our_prob, 4),
                                "odds": odds,
                                "ev": round(ev, 4),
                                "kelly": round(kelly, 4),
                                "stake": round(stake, 2),
                                "won": won,
                                "pnl": round(pnl, 2),
                                "actual_outcome": actual_ou,
                            })
            except Exception as e:
                logger.debug(f"OU prediction error for {fid}: {e}")

    return bets


# ── Metrics ───────────────────────────────────────────────────────────────────

def bootstrap_roi(bets: List[dict], n_boot: int = 2000, seed: int = 42) -> Tuple[float, float, float]:
    """
    Return (roi_point, ci_low_95, ci_high_95) via bootstrap resampling.
    ROI = total_pnl / total_staked.
    """
    if not bets:
        return 0.0, 0.0, 0.0

    pnls = np.array([b["pnl"] for b in bets])
    stakes = np.array([b["stake"] for b in bets])
    total_staked = stakes.sum()
    if total_staked == 0:
        return 0.0, 0.0, 0.0

    point_roi = pnls.sum() / total_staked

    rng = np.random.default_rng(seed)
    boot_rois = []
    n = len(bets)
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        s = stakes[idx].sum()
        if s > 0:
            boot_rois.append(pnls[idx].sum() / s)

    boot_rois = np.array(boot_rois)
    ci_low = float(np.percentile(boot_rois, 2.5))
    ci_high = float(np.percentile(boot_rois, 97.5))
    return float(point_roi), ci_low, ci_high


def calibration_deciles(bets: List[dict]) -> List[dict]:
    """
    Group bets into 10 deciles by our_prob, report mean_prob vs actual_win_rate.
    """
    if len(bets) < 20:
        return []

    sorted_bets = sorted(bets, key=lambda b: b["our_prob"])
    n = len(sorted_bets)
    decile_size = max(1, n // 10)

    result = []
    for i in range(10):
        chunk = sorted_bets[i * decile_size: (i + 1) * decile_size]
        if not chunk:
            continue
        mean_prob = float(np.mean([b["our_prob"] for b in chunk]))
        actual_rate = float(np.mean([1.0 if b["won"] else 0.0 for b in chunk]))
        result.append({
            "decile": i + 1,
            "n": len(chunk),
            "mean_predicted_prob": round(mean_prob, 4),
            "actual_win_rate": round(actual_rate, 4),
        })
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("=== Bootball Walk-Forward Backtest ===")
    logger.info(f"DB: {DB_PATH}")
    logger.info(f"Output: {OUTPUT_PATH}")

    # 1. Load data
    all_fixtures = load_all_fixtures()
    odds_fixtures = load_odds_fixtures()
    odds_by_id = {f["id"]: f for f in odds_fixtures}

    # 2. Build team history index (point-in-time feature source)
    logger.info("Building point-in-time team history index...")
    history = build_team_history(all_fixtures)

    # Map: league_id → set of team_ids (for rank computation)
    league_teams: Dict[int, List[int]] = {}
    for f in all_fixtures:
        lid = f["league_id"]
        if lid not in league_teams:
            league_teams[lid] = set()
        league_teams[lid].add(f["home_team_id"])
        league_teams[lid].add(f["away_team_id"])
    league_teams = {k: list(v) for k, v in league_teams.items()}

    # 3. Build feature vectors for all odds-bearing fixtures
    # (These are the test candidates; we compute features once, reuse across windows)
    logger.info(f"Computing point-in-time features for {len(odds_fixtures):,} test fixtures...")
    feature_map: Dict[int, np.ndarray] = {}
    for i, f in enumerate(odds_fixtures):
        if i % 200 == 0 and i > 0:
            logger.info(f"  Features: {i}/{len(odds_fixtures)}")
        vec = build_features_for_fixture(f, history, league_teams)
        if vec is not None:
            feature_map[f["id"]] = vec
    logger.info(f"Feature vectors built: {len(feature_map):,}")

    # 4. Walk-forward windows
    all_bets: List[dict] = []
    window_reports = []

    for w_idx, window_start in enumerate(WINDOW_STARTS):
        window_end = WINDOW_STARTS[w_idx + 1] if w_idx + 1 < len(WINDOW_STARTS) else WINDOW_END
        cutoff_str = window_start.strftime("%Y-%m-%d")
        window_end_str = window_end.strftime("%Y-%m-%d")

        logger.info(f"\n--- Window {w_idx+1}: train<{cutoff_str}, test=[{cutoff_str}, {window_end_str}) ---")

        # Training set: all fixtures with date < cutoff
        train_fixtures = [
            f for f in all_fixtures
            if f["date"] < cutoff_str
        ]
        # Test set: odds fixtures in [window_start, window_end)
        test_fixtures = [
            f for f in odds_fixtures
            if cutoff_str <= f["date"] < window_end_str
            and f["id"] in feature_map
        ]

        logger.info(f"  Train: {len(train_fixtures):,} fixtures | Test: {len(test_fixtures):,} fixtures")

        if len(train_fixtures) < 100 or len(test_fixtures) == 0:
            logger.warning(f"  Skipping window {w_idx+1} — insufficient data")
            continue

        # Build training feature matrix
        X_list, y_h2h_list, y_btts_list, y_ou_list = [], [], [], []
        for f in train_fixtures:
            vec = build_features_for_fixture(f, history, league_teams)
            if vec is None:
                continue
            lbl_h2h = h2h_label(f)
            if lbl_h2h < 0:
                continue
            X_list.append(vec)
            y_h2h_list.append(lbl_h2h)
            y_btts_list.append(btts_label(f))
            y_ou_list.append(ou_label(f))

        if not X_list:
            logger.warning(f"  No valid training samples for window {w_idx+1}")
            continue

        X_train = np.array(X_list)
        y_h2h = np.array(y_h2h_list)
        y_btts = np.array(y_btts_list)
        y_ou = np.array(y_ou_list)

        logger.info(f"  Training matrix: {X_train.shape}")

        # Fit models
        model_h2h = fit_model("h2h", X_train, y_h2h)
        model_btts = fit_model("btts", X_train, y_btts)
        model_ou = fit_model("over_under", X_train, y_ou)

        # Simulate bets
        bankroll = STARTING_BANKROLL
        window_bets = simulate_bets_for_window(
            model_h2h, model_btts, model_ou,
            test_fixtures,
            feature_map,
            bankroll,
        )
        all_bets.extend(window_bets)

        logger.info(f"  Bets placed: {len(window_bets)}")
        if window_bets:
            total_staked = sum(b["stake"] for b in window_bets)
            total_pnl = sum(b["pnl"] for b in window_bets)
            win_rate = sum(1 for b in window_bets if b["won"]) / len(window_bets)
            roi = total_pnl / total_staked if total_staked > 0 else 0
            logger.info(f"  PnL={total_pnl:.2f}, Staked={total_staked:.2f}, ROI={roi:.1%}, Win%={win_rate:.1%}")

        window_reports.append({
            "window": w_idx + 1,
            "train_cutoff": cutoff_str,
            "test_start": cutoff_str,
            "test_end": window_end_str,
            "train_n": len(X_train),
            "test_n": len(test_fixtures),
            "bets_placed": len(window_bets),
        })

    # 5. Aggregate metrics
    logger.info(f"\n=== AGGREGATE RESULTS ===")
    logger.info(f"Total bets: {len(all_bets)}")

    # Per-market breakdown
    market_results = {}
    for market in ["h2h", "btts", "over_under"]:
        mbets = [b for b in all_bets if b["market"] == market]
        if not mbets:
            market_results[market] = {"n_bets": 0}
            continue

        roi, ci_low, ci_high = bootstrap_roi(mbets)
        total_staked = sum(b["stake"] for b in mbets)
        total_pnl = sum(b["pnl"] for b in mbets)
        win_rate = sum(1 for b in mbets if b["won"]) / len(mbets)
        avg_odds = float(np.mean([b["odds"] for b in mbets]))
        avg_ev = float(np.mean([b["ev"] for b in mbets]))

        cal = calibration_deciles(mbets)

        market_results[market] = {
            "n_bets": len(mbets),
            "total_staked": round(total_staked, 2),
            "total_pnl": round(total_pnl, 2),
            "roi_point": round(roi, 4),
            "roi_ci_95_low": round(ci_low, 4),
            "roi_ci_95_high": round(ci_high, 4),
            "win_rate": round(win_rate, 4),
            "avg_odds": round(avg_odds, 3),
            "avg_ev_at_selection": round(avg_ev, 4),
            "ci_includes_zero": ci_low < 0 < ci_high,
            "calibration_deciles": cal,
        }

        logger.info(
            f"  {market}: n={len(mbets)}, ROI={roi:.1%} "
            f"95%CI=[{ci_low:.1%}, {ci_high:.1%}], "
            f"win%={win_rate:.1%}, avg_odds={avg_odds:.2f}"
        )

    # Overall
    overall_roi, overall_ci_low, overall_ci_high = bootstrap_roi(all_bets)
    total_staked_all = sum(b["stake"] for b in all_bets)
    total_pnl_all = sum(b["pnl"] for b in all_bets)

    # 6. Write results
    results = {
        "generated_at": datetime.utcnow().isoformat(),
        "methodology": {
            "features": "9-feature point-in-time standings from fixtures table (NO leakage from standings table)",
            "feature_vector": "[h_rank, a_rank, h_gf-h_ga, a_gf-a_ga, h_gf, a_gf, h_ga, a_ga, |h_rank-a_rank|]",
            "model": "LightGBMClassifier (n_estimators=200, num_leaves=31, lr=0.05)",
            "calibration": "none (raw probabilities used for EV — same as production V1)",
            "ev_threshold": BOT_MIN_EV,
            "kelly_fraction": KELLY_FRACTION,
            "min_bet": MIN_BET,
            "starting_bankroll": STARTING_BANKROLL,
            "data_scope": "2025-season FT fixtures (Apr 15–Jun 16 2026 for test, full season for train)",
            "window_type": "expanding (all history before cutoff)",
            "odds_source": "fixture_odds table (pre-match odds stored by odds_poll.py)",
        },
        "data_summary": {
            "total_train_pool": len(all_fixtures),
            "total_odds_fixtures": len(odds_fixtures),
            "feature_vectors_built": len(feature_map),
        },
        "walk_forward_windows": window_reports,
        "overall": {
            "n_bets": len(all_bets),
            "total_staked": round(total_staked_all, 2),
            "total_pnl": round(total_pnl_all, 2),
            "roi_point": round(overall_roi, 4),
            "roi_ci_95_low": round(overall_ci_low, 4),
            "roi_ci_95_high": round(overall_ci_high, 4),
            "ci_includes_zero": overall_ci_low < 0 < overall_ci_high,
        },
        "by_market": market_results,
        "all_bets": all_bets,
    }

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    logger.info(f"\nResults written to {OUTPUT_PATH}")

    # Print summary table
    print("\n" + "=" * 65)
    print("WALK-FORWARD BACKTEST SUMMARY")
    print("=" * 65)
    print(f"{'Market':<14} {'Bets':>5} {'Staked':>9} {'PnL':>9} {'ROI':>8}  {'95% CI'}")
    print("-" * 65)
    for market in ["h2h", "btts", "over_under"]:
        r = market_results[market]
        if r["n_bets"] == 0:
            print(f"{market:<14} {'0':>5} {'–':>9} {'–':>9} {'–':>8}")
            continue
        ci_flag = " ⚠ CI∋0" if r["ci_includes_zero"] else " ✓"
        print(
            f"{market:<14} {r['n_bets']:>5} {r['total_staked']:>9.2f} "
            f"{r['total_pnl']:>9.2f} {r['roi_point']:>7.1%}  "
            f"[{r['roi_ci_95_low']:.1%}, {r['roi_ci_95_high']:.1%}]{ci_flag}"
        )
    print("-" * 65)
    if all_bets:
        print(
            f"{'TOTAL':<14} {len(all_bets):>5} {total_staked_all:>9.2f} "
            f"{total_pnl_all:>9.2f} {overall_roi:>7.1%}  "
            f"[{overall_ci_low:.1%}, {overall_ci_high:.1%}]"
        )
    else:
        print("No bets qualified across all windows.")
    print("=" * 65)
    print(f"\nFull results (including per-bet log) → {OUTPUT_PATH}")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    main()
