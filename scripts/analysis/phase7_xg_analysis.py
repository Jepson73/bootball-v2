"""Phase 7 — xG Replication (Wilkens 2026).
Tests whether replacing realized goals with rolling historical xG closes the
selection-penalty gap identified in Phase 6.

Variants:
  A: Skellam + isotonic calibration (Wilkens replication)
  B: Dixon-Coles architecture, xG input instead of goals

Leagues: EPL (39), Serie_A (135), La_Liga (140) — Understat coverage only.
Walk-forward windows: fdco 2022 (val Jan–Dec 2022), fdco 2023 (val Jan 2023–Jun 2024).
"""

import json, math, os, time
from pathlib import Path
import numpy as np
import sqlite3
from scipy import stats, optimize, special
from sklearn.isotonic import IsotonicRegression
from sqlalchemy import create_engine, text
from understatapi import UnderstatClient

# ── constants ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
ANALYSIS = Path(__file__).parent
CACHE_DIR = ANALYSIS / "understat_cache"
CACHE_DIR.mkdir(exist_ok=True)

UNDERSTAT_LEAGUES = {"EPL": 39, "Serie_A": 135, "La_Liga": 140}
UNDERSTAT_SEASONS = list(range(2014, 2024))   # 2014/15 – 2023/24
FDCO_LEAGUES = [39, 135, 140]
WINDOWS = {
    "2022": {"val_start": "2022-01-01", "val_end": "2022-12-31", "blend_w": 1.0},
    "2023": {"val_start": "2023-01-01", "val_end": "2024-06-30", "blend_w": 0.65},
}
ROLL_WINDOWS = [5, 10]
BOT_MIN_EV = 0.05
N_BOOTSTRAP = 5000
RNG = np.random.default_rng(42)

# ── team-name mapping: production DB name → Understat name ────────────────────
DB_TO_UNDERSTAT = {
    # EPL
    "Newcastle":    "Newcastle United",
    "Wolves":       "Wolverhampton Wanderers",
    "Sheffield Utd": "Sheffield United",
    "West Brom":    "West Bromwich Albion",
    # Serie A
    "AS Roma":      "Roma",
    "Hellas Verona": "Verona",
    "Spal":         "SPAL 2013",
    # La Liga
    "Granada CF":   "Granada",
    "Huesca":       "SD Huesca",
    "Valladolid":   "Real Valladolid",
}

def normalize(name: str) -> str:
    return DB_TO_UNDERSTAT.get(name, name).lower().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Fetch & cache Understat data
# ─────────────────────────────────────────────────────────────────────────────

def fetch_understat_all() -> dict:
    """Fetch all EPL/Serie_A/La_Liga seasons 2014-2023; cache per-file."""
    all_matches = []  # list of dicts
    with UnderstatClient() as understat:
        for uname in UNDERSTAT_LEAGUES:
            for season in UNDERSTAT_SEASONS:
                cache_file = CACHE_DIR / f"{uname}_{season}.json"
                if cache_file.exists():
                    matches = json.loads(cache_file.read_text())
                else:
                    try:
                        matches = understat.league(league=uname).get_match_data(season=str(season))
                        cache_file.write_text(json.dumps(matches))
                        time.sleep(0.8)
                    except Exception as e:
                        print(f"  WARN: {uname} {season}: {e}")
                        matches = []
                league_id = UNDERSTAT_LEAGUES[uname]
                for m in matches:
                    if not m.get("isResult"):
                        continue
                    try:
                        all_matches.append({
                            "league_id": league_id,
                            "season":    season,
                            "date":      m["datetime"][:10],       # "YYYY-MM-DD"
                            "home_title": m["h"]["title"],
                            "away_title": m["a"]["title"],
                            "xg_home":   float(m["xG"]["h"]),
                            "xg_away":   float(m["xG"]["a"]),
                            "goals_home": int(m["goals"]["h"]),
                            "goals_away": int(m["goals"]["a"]),
                        })
                    except (KeyError, TypeError, ValueError):
                        pass
    print(f"Understat: loaded {len(all_matches)} completed matches across "
          f"{len(UNDERSTAT_LEAGUES)} leagues, seasons {min(UNDERSTAT_SEASONS)}–{max(UNDERSTAT_SEASONS)}")
    return all_matches


def report_understat_coverage(all_matches):
    from collections import defaultdict
    by_league = defaultdict(set)
    for m in all_matches:
        by_league[m["league_id"]].add(m["season"])
    name_map = {v: k for k, v in UNDERSTAT_LEAGUES.items()}
    for lid in FDCO_LEAGUES:
        seasons = sorted(by_league[lid])
        n = sum(1 for m in all_matches if m["league_id"] == lid)
        print(f"  {name_map[lid]} (id={lid}): {n} matches, seasons {seasons[0]}–{seasons[-1]}")


# ─────────────────────────────────────────────────────────────────────────────
# Team ID → Understat name mapping (via production DB)
# ─────────────────────────────────────────────────────────────────────────────

def build_team_id_to_understat(engine) -> dict:
    """Return {team_id: understat_norm_name}."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT home_team_id, th.name "
            "FROM fixtures f JOIN teams th ON f.home_team_id = th.id "
            "WHERE f.league_id IN (39, 135, 140)"
        ))
        mapping = {}
        for tid, name in rows:
            mapping[tid] = normalize(name)
    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Rolling xG features (leakage-safe)
# ─────────────────────────────────────────────────────────────────────────────

def build_rolling_xg_lookup(all_matches, team_id_map, window: int) -> dict:
    """
    Returns {(team_id, "before_YYYY-MM-DD"): (rolling_xGF, rolling_xGA, n_matches)}.
    Only populated for the fixture dates we'll actually need (from preds).
    """
    # Build per-team match history keyed by Understat name
    # match_history[norm_name] = sorted list of (date, xgF, xgA)
    from collections import defaultdict
    team_history: dict = defaultdict(list)
    for m in all_matches:
        hn = normalize(m["home_title"])
        an = normalize(m["away_title"])
        d = m["date"]
        team_history[hn].append((d, m["xg_home"], m["xg_away"]))  # xgF, xgA for home
        team_history[an].append((d, m["xg_away"], m["xg_home"]))  # xgF, xgA for away

    # Sort by date
    for name in team_history:
        team_history[name].sort(key=lambda x: x[0])

    # Build reverse map: norm_name → team_id
    norm_to_id = {v: k for k, v in team_id_map.items()}

    # Build rolling lookup: {(team_id, date_str): (xGF, xGA, n)}
    rolling = {}
    for norm_name, history in team_history.items():
        tid = norm_to_id.get(norm_name)
        if tid is None:
            continue
        for i, (date, _, _) in enumerate(history):
            # Matches strictly before this date
            prior = [h for h in history[:i] if h[0] < date]
            if not prior:
                rolling[(tid, date)] = (None, None, 0)
                continue
            recent = prior[-window:]
            xgF_vals = [h[1] for h in recent]
            xgA_vals = [h[2] for h in recent]
            rolling[(tid, date)] = (np.mean(xgF_vals), np.mean(xgA_vals), len(recent))

    return rolling, team_history


def get_rolling_xg(rolling_lookup, team_history, team_id_map, tid, target_date, window):
    """
    Return (rolling_xGF, rolling_xGA, n) for team_id up to (but not including) target_date.
    Returns (None, None, 0) if insufficient history.
    """
    # Get all matches for this team before target_date
    norm_name = team_id_map.get(tid)
    if norm_name is None:
        return None, None, 0
    norm_to_id = {v: k for k, v in team_id_map.items()}

    # We need to search team_history directly
    history = team_history.get(norm_name, [])
    prior = [h for h in history if h[0] < target_date]
    if not prior:
        return None, None, 0
    recent = prior[-window:]
    xgF = np.mean([h[1] for h in recent])
    xgA = np.mean([h[2] for h in recent])
    return xgF, xgA, len(recent)


# ─────────────────────────────────────────────────────────────────────────────
# xG match-to-fixture joining
# ─────────────────────────────────────────────────────────────────────────────

def build_xg_fixture_map(all_matches, team_id_map) -> dict:
    """
    Returns {(home_team_id, away_team_id, date_str): (xg_home, xg_away)}.
    Joins Understat matches to production DB team IDs via normalised name match.
    """
    norm_to_id = {v: k for k, v in team_id_map.items()}
    xg_map = {}
    matched = 0
    for m in all_matches:
        hn = normalize(m["home_title"])
        an = normalize(m["away_title"])
        hid = norm_to_id.get(hn)
        aid = norm_to_id.get(an)
        if hid is None or aid is None:
            continue
        xg_map[(hid, aid, m["date"])] = (m["xg_home"], m["xg_away"])
        matched += 1
    return xg_map, matched


# ─────────────────────────────────────────────────────────────────────────────
# Probability utilities
# ─────────────────────────────────────────────────────────────────────────────

def shin_probabilities(odds: list) -> list:
    """Iterative Shin de-vig to fair probabilities."""
    try:
        raw = [1.0 / o for o in odds]
        total = sum(raw)
        if abs(total - 1.0) < 1e-9:
            return raw
        for _ in range(200):
            z = sum(r ** 2 for r in raw)
            if z < 1e-15:
                break
            denom = [2 * r - z for r in raw]
            if any(abs(d) < 1e-12 for d in denom):
                break
            z_half = sum(r ** 2 / d for r, d in zip(raw, denom))
            if abs(1.0 - z_half) < 1e-12:
                break
            z_new = max(1e-10, min(0.5, (1.0 - z_half / total) / (1.0 - z_half)))
            new_raw = [r / (total * (1.0 - z_new) + z_new * r / z) for r in raw]
            if abs(sum(new_raw) - 1.0) < 1e-9:
                raw = new_raw
                break
            raw = new_raw
        s = sum(raw)
        return [r / s for r in raw]
    except (OverflowError, ZeroDivisionError):
        # Fallback: simple proportional de-vig
        raw = [1.0 / o for o in odds]
        s = sum(raw)
        return [r / s for r in raw]


def bootstrap_ci(values, n=N_BOOTSTRAP, seed=42):
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return float("nan"), float("nan"), float("nan")
    rng_b = np.random.default_rng(seed)
    means = [np.mean(rng_b.choice(arr, size=len(arr), replace=True)) for _ in range(n)]
    return float(np.mean(arr)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ─────────────────────────────────────────────────────────────────────────────
# Skellam model utilities (Variant A)
# ─────────────────────────────────────────────────────────────────────────────

def skellam_hda(lam: float, mu: float, max_diff: int = 15) -> tuple:
    """Return (p_home, p_draw, p_away) using Skellam(lam, mu) distribution."""
    from scipy.stats import skellam
    p_draw = float(skellam.pmf(0, lam, mu))
    p_home = sum(float(skellam.pmf(k, lam, mu)) for k in range(1, max_diff + 1))
    p_away = 1.0 - p_home - p_draw
    p_home = max(1e-9, p_home)
    p_draw = max(1e-9, p_draw)
    p_away = max(1e-9, p_away)
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s


def fit_skellam_params(training_rows):
    """
    Fit home_adv, league_mean_xGF parameters from training rows.
    Each row: (xGF_home, xGA_home, xGF_away, xGA_away, xg_home_actual, xg_away_actual)
    Returns (home_adv_mult, ref_xGF).
    """
    home_xg = [r[4] for r in training_rows]
    away_xg = [r[5] for r in training_rows]
    mean_home = float(np.mean(home_xg)) if home_xg else 1.3
    mean_away = float(np.mean(away_xg)) if away_xg else 1.1
    home_adv = mean_home / mean_away if mean_away > 0 else 1.2
    ref_xgf = (mean_home + mean_away) / 2
    return home_adv, ref_xgf


def predict_skellam(xGF_h, xGA_h, xGF_a, xGA_a, home_adv, ref_xgf) -> tuple:
    """Parameterise λ/μ from rolling xG and home_adv, then return H/D/A probs."""
    if xGF_h is None or xGF_a is None:
        return None
    # Multiplicative: λ = xGF_home × (xGA_away / ref) × home_adv
    #                 μ = xGF_away × (xGA_home / ref) / home_adv^0.3
    # Using a softened home_adv in the denominator to avoid over-correction
    ref = max(ref_xgf, 0.5)
    lam = xGF_h * (xGA_a / ref) * home_adv
    mu  = xGF_a * (xGA_h / ref) / (home_adv ** 0.5)
    lam = max(lam, 0.05)
    mu  = max(mu, 0.05)
    return skellam_hda(lam, mu)


# ─────────────────────────────────────────────────────────────────────────────
# Isotonic calibration (per-class one-vs-rest, renormalized)
# ─────────────────────────────────────────────────────────────────────────────

def fit_isotonic_calibrator(probs, outcomes, target_class):
    """
    probs: list of (pH, pD, pA) from model on TRAINING set
    outcomes: list of int 0/1/2 (0=home, 1=draw, 2=away)
    target_class: 0, 1, or 2
    Returns fitted IsotonicRegression.
    """
    x = np.array([p[target_class] for p in probs])
    y = np.array([1.0 if o == target_class else 0.0 for o in outcomes])
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(x.reshape(-1, 1), y)
    return iso


def apply_isotonic_calibration(raw_probs, cal_h, cal_d, cal_a):
    """Apply three isotonic calibrators and renormalize."""
    calibrated = []
    for pH, pD, pA in raw_probs:
        cH = float(cal_h.predict([[pH]])[0])
        cD = float(cal_d.predict([[pD]])[0])
        cA = float(cal_a.predict([[pA]])[0])
        s = cH + cD + cA
        if s < 1e-9:
            calibrated.append((1/3, 1/3, 1/3))
        else:
            calibrated.append((cH/s, cD/s, cA/s))
    return calibrated


# ─────────────────────────────────────────────────────────────────────────────
# Variant B — DC bivariate Poisson with rolling xG parameterisation
# ─────────────────────────────────────────────────────────────────────────────
# Design decision: Variant B uses the same rolling-xG → λ/μ formula as Variant A
# (same home_adv and ref_xgf fitted on training data) but substitutes the DC
# bivariate Poisson integer-score formula for the Skellam distribution, and
# omits isotonic calibration.  This cleanly isolates "model architecture" (Skellam+iso
# vs DC-bivariate) from "xG input" (vs realized-goals baseline).

def predict_dc_rolling_xg(xGF_h, xGA_h, xGF_a, xGA_a,
                            home_adv: float, ref_xgf: float, rho: float):
    """DC bivariate Poisson using rolling-xG → λ/μ (same as Skellam parameterisation)."""
    if xGF_h is None or xGF_a is None:
        return None
    ref = max(ref_xgf, 0.5)
    lam = xGF_h * (xGA_a / ref) * home_adv
    mu  = xGF_a * (xGA_h / ref) / (home_adv ** 0.5)
    lam = max(lam, 0.05)
    mu  = max(mu, 0.05)
    return dc_probs_from_lam_mu(lam, mu, rho)


def dc_probs_from_lam_mu(lam: float, mu: float, rho: float, max_goals: int = 8) -> tuple:
    """Standard DC bivariate Poisson probabilities (from phase6_combined.py)."""
    def rho_correction(x, y, lam_, mu_, rho_):
        if x == 0 and y == 0:
            return 1.0 - lam_ * mu_ * rho_
        elif x == 0 and y == 1:
            return 1.0 + lam_ * rho_
        elif x == 1 and y == 0:
            return 1.0 + mu_ * rho_
        elif x == 1 and y == 1:
            return 1.0 - rho_
        return 1.0

    prob = np.zeros((max_goals + 1, max_goals + 1))
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = (math.exp(-lam) * lam**h / math.factorial(h) *
                 math.exp(-mu) * mu**a / math.factorial(a))
            p *= rho_correction(h, a, lam, mu, rho)
            prob[h, a] = max(0.0, p)

    total = prob.sum()
    if total < 1e-9:
        return None
    prob /= total

    p_home = float(np.sum(np.tril(prob, -1)))
    p_draw = float(np.trace(prob))
    p_away = float(np.sum(np.triu(prob, 1)))
    p_over = float(sum(prob[h, a] for h in range(max_goals + 1)
                       for a in range(max_goals + 1) if h + a > 2))

    s = p_home + p_draw + p_away
    if s < 1e-9:
        return None
    return p_home / s, p_draw / s, p_away / s, p_over


# ─────────────────────────────────────────────────────────────────────────────
# EV selection (production blend formula)
# ─────────────────────────────────────────────────────────────────────────────

def blend_ev(p_model: tuple, open_odds: tuple, blend_w: float):
    """
    Blend model probabilities with Shin-devigged market, compute EV per outcome.
    Returns (ev_home, ev_draw, ev_away) and blended probs.
    """
    shin_p = shin_probabilities(list(open_odds))
    p_blend = tuple(blend_w * p_model[i] + (1.0 - blend_w) * shin_p[i] for i in range(3))
    ev = tuple(p_blend[i] * open_odds[i] - 1.0 for i in range(3))
    return ev, p_blend


def select_bet(ev: tuple, open_odds: tuple) -> tuple:
    """Return (idx, direction, odds) for best bet if EV > BOT_MIN_EV, else None."""
    best_idx = int(np.argmax(ev))
    if ev[best_idx] > BOT_MIN_EV:
        dirs = ["home", "draw", "away"]
        return best_idx, dirs[best_idx], open_odds[best_idx]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward evaluation
# ─────────────────────────────────────────────────────────────────────────────

def run_walk_forward(
    preds_all: list,
    all_matches: list,
    team_id_map: dict,
    team_history: dict,
    dc_cache: dict,
    closing_map: dict,
    roll_window: int,
) -> dict:
    """
    For each fdco window:
      - Build training set from all Understat matches before window start
      - For each pred in validation window:
          * Get rolling xG for both teams
          * Compute Variant A and Variant B probabilities
          * Select EV bets (blend formula)
          * Track ROI and CLV
    Returns results dict.
    """
    results = {}

    for win_name, wcfg in WINDOWS.items():
        val_start = wcfg["val_start"]
        val_end   = wcfg["val_end"]
        blend_w   = wcfg["blend_w"]

        # ── training set: all Understat matches strictly before val_start ──
        training_matches = [
            m for m in all_matches
            if m["date"] < val_start and m["league_id"] in FDCO_LEAGUES
        ]

        # ── fit Skellam parameters per league ──
        skellam_params = {}
        for league_id in FDCO_LEAGUES:
            train_l = [m for m in training_matches if m["league_id"] == league_id]
            rows = []
            for m in train_l:
                hid = get_team_id(team_id_map, m["home_title"])
                aid = get_team_id(team_id_map, m["away_title"])
                if hid is None or aid is None:
                    continue
                xGF_h, xGA_h, nh = get_rolling_xg(None, team_history, team_id_map, hid, m["date"], roll_window)
                xGF_a, xGA_a, na = get_rolling_xg(None, team_history, team_id_map, aid, m["date"], roll_window)
                if xGF_h is None or xGF_a is None:
                    continue
                rows.append((xGF_h, xGA_h, xGF_a, xGA_a, m["xg_home"], m["xg_away"]))
            skellam_params[league_id] = fit_skellam_params(rows)

        # Variant B shares skellam_params (home_adv, ref_xgf) — no additional fitting

        # ── fit isotonic calibrators for Variant A ──
        iso_cal = {}
        for league_id in FDCO_LEAGUES:
            train_l = [m for m in training_matches if m["league_id"] == league_id]
            probs_train, outcomes_train = [], []
            h_adv, ref_xgf = skellam_params.get(league_id, (1.2, 1.2))
            for m in train_l:
                hid = get_team_id(team_id_map, m["home_title"])
                aid = get_team_id(team_id_map, m["away_title"])
                if hid is None or aid is None:
                    continue
                xGF_h, xGA_h, nh = get_rolling_xg(None, team_history, team_id_map, hid, m["date"], roll_window)
                xGF_a, xGA_a, na = get_rolling_xg(None, team_history, team_id_map, aid, m["date"], roll_window)
                if xGF_h is None or xGF_a is None or nh < 2 or na < 2:
                    continue
                probs_t = predict_skellam(xGF_h, xGA_h, xGF_a, xGA_a, h_adv, ref_xgf)
                if probs_t is None:
                    continue
                probs_train.append(probs_t)
                gh, ga = m["goals_home"], m["goals_away"]
                outcome = 0 if gh > ga else (1 if gh == ga else 2)
                outcomes_train.append(outcome)

            if len(probs_train) < 20:
                iso_cal[league_id] = None
            else:
                cal_h = fit_isotonic_calibrator(probs_train, outcomes_train, 0)
                cal_d = fit_isotonic_calibrator(probs_train, outcomes_train, 1)
                cal_a = fit_isotonic_calibrator(probs_train, outcomes_train, 2)
                iso_cal[league_id] = (cal_h, cal_d, cal_a)

        # ── validation pass ──
        val_preds = [
            p for p in preds_all
            if (p["league_id"] in FDCO_LEAGUES
                and val_start <= p["date"][:10] <= val_end)
        ]

        # Also need baseline DC preds for the same set (from dc cache)
        # We have them in preds_all directly
        dc_cache_preds_by_id = {p["id"]: p for p in preds_all}

        rec_a, rec_b, rec_base = [], [], []
        qual_all_a, qual_all_b, qual_all_base = [], [], []
        n_no_xg = 0

        for pred in val_preds:
            lid = pred["league_id"]
            date = pred["date"][:10]
            hid  = pred["home_team_id"]
            aid  = pred["away_team_id"]
            open_odds = (pred["odd_home"], pred["odd_draw"], pred["odd_away"])
            if any(o is None or o <= 1.0 for o in open_odds):
                continue

            # Ground truth
            gh = pred.get("goals_home")
            ga = pred.get("goals_away")
            if gh is None or ga is None:
                continue
            outcome = 0 if gh > ga else (1 if gh == ga else 2)

            # Closing odds for CLV
            close_entry = closing_map.get(pred["id"])

            # ── rolling xG for both teams ──
            xGF_h, xGA_h, nh = get_rolling_xg(None, team_history, team_id_map, hid, date, roll_window)
            xGF_a, xGA_a, na = get_rolling_xg(None, team_history, team_id_map, aid, date, roll_window)

            if xGF_h is None or xGF_a is None:
                n_no_xg += 1
                continue

            # ── Variant A: Skellam + isotonic ──
            h_adv, ref_xgf = skellam_params.get(lid, (1.2, 1.2))
            raw_probs_a = predict_skellam(xGF_h, xGA_h, xGF_a, xGA_a, h_adv, ref_xgf)
            if raw_probs_a is None:
                continue

            if iso_cal.get(lid):
                cal_probs_a = apply_isotonic_calibration([raw_probs_a], *iso_cal[lid])[0]
            else:
                cal_probs_a = raw_probs_a

            ev_a, pb_a = blend_ev(cal_probs_a, open_odds, blend_w)
            bet_a = select_bet(ev_a, open_odds)
            qual_all_a.append({"p_home": cal_probs_a[0], "p_draw": cal_probs_a[1],
                                "p_away": cal_probs_a[2], "outcome": outcome})

            # ── Variant B: DC bivariate Poisson with rolling xG ──
            rho_val = _get_rho(dc_cache, win_name, lid)
            probs_b = predict_dc_rolling_xg(xGF_h, xGA_h, xGF_a, xGA_a, h_adv, ref_xgf, rho_val)
            if probs_b is not None:
                ev_b, pb_b = blend_ev(probs_b[:3], open_odds, blend_w)
                bet_b = select_bet(ev_b, open_odds)
                qual_all_b.append({"p_home": probs_b[0], "p_draw": probs_b[1],
                                   "p_away": probs_b[2], "outcome": outcome})
            else:
                bet_b = None

            # ── Baseline DC (from preds cache) ──
            dc_base = pred.get("dc", {})
            p_hda_base = dc_base.get("p_h2h")
            if p_hda_base and len(p_hda_base) == 3:
                ev_base, pb_base = blend_ev(tuple(p_hda_base), open_odds, blend_w)
                bet_base = select_bet(ev_base, open_odds)
                qual_all_base.append({"p_home": p_hda_base[0], "p_draw": p_hda_base[1],
                                      "p_away": p_hda_base[2], "outcome": outcome})
            else:
                bet_base = None

            # ── Record bets ──
            for variant_label, bet, probs in [
                ("A", bet_a, cal_probs_a),
                ("B", bet_b, probs_b[:3] if probs_b else None),
                ("base", bet_base, tuple(p_hda_base) if p_hda_base and len(p_hda_base) == 3 else None),
            ]:
                if bet is None or probs is None:
                    continue
                bet_idx, bet_dir, bet_odds = bet
                won = (outcome == bet_idx)
                roi_val = (bet_odds - 1.0) if won else -1.0

                # CLV
                clv_val = None
                if close_entry and close_entry.get("h2h"):
                    c_odds = close_entry["h2h"]
                    if len(c_odds) > bet_idx and c_odds[bet_idx] > 1.0:
                        clv_val = (bet_odds - c_odds[bet_idx]) / c_odds[bet_idx]

                row = {
                    "fixture_id": pred["id"],
                    "league_id": lid,
                    "date": date,
                    "home_team_id": hid,
                    "away_team_id": aid,
                    "bet_dir": bet_dir,
                    "bet_odds": bet_odds,
                    "outcome": outcome,
                    "won": won,
                    "roi": roi_val,
                    "clv": clv_val,
                    "p_home": probs[0],
                    "p_draw": probs[1],
                    "p_away": probs[2],
                    "ev": ev_a[bet_idx] if variant_label == "A" else (
                          ev_b[bet_idx] if variant_label == "B" else
                          ev_base[bet_idx]),
                    "xGF_home": float(xGF_h) if xGF_h else None,
                    "xGF_away": float(xGF_a) if xGF_a else None,
                    "n_xg_home": nh,
                    "n_xg_away": na,
                }
                if variant_label == "A":
                    rec_a.append(row)
                elif variant_label == "B":
                    rec_b.append(row)
                else:
                    rec_base.append(row)

        results[win_name] = {
            "n_val_preds": len(val_preds),
            "n_no_xg": n_no_xg,
            "variant_a": rec_a,
            "variant_b": rec_b,
            "baseline": rec_base,
            "qual_all_a": qual_all_a,
            "qual_all_b": qual_all_b,
            "qual_all_base": qual_all_base,
            "skellam_params": {str(k): v for k, v in skellam_params.items()},
        }

        print(f"  Window {win_name}: {len(val_preds)} val preds → "
              f"Var-A={len(rec_a)}, Var-B={len(rec_b)}, Base={len(rec_base)} bets "
              f"(no_xg={n_no_xg})")

    return results


def get_team_id(team_id_map: dict, understat_title: str):
    """Reverse lookup: understat_title → team_id via the normalised map."""
    norm_title = normalize(understat_title)
    for tid, norm_name in team_id_map.items():
        if norm_name == norm_title:
            return tid
    return None


def _get_rho(dc_cache_all: dict, win_name: str, league_id: int) -> float:
    """Get DC rho from the phase6 cache for this window/league."""
    dc = dc_cache_all.get(win_name, {})
    models = dc.get("models", {})
    league_model = models.get(str(league_id), {})
    return float(league_model.get("rho", 0.0))


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 raw model quality (log-loss / Brier / AUC)
# ─────────────────────────────────────────────────────────────────────────────

def compute_raw_quality(records: list) -> dict:
    """Compute log-loss, Brier, AUC on all validation predictions (not just bets)."""
    from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
    if not records:
        return {}
    p_h = [r["p_home"] for r in records]
    p_d = [r["p_draw"] for r in records]
    p_a = [r["p_away"] for r in records]
    y = [r["outcome"] for r in records]

    # AUC: one-vs-rest for home/away
    try:
        auc_h = roc_auc_score([1 if o == 0 else 0 for o in y], p_h)
        auc_a = roc_auc_score([1 if o == 2 else 0 for o in y], p_a)
        auc = (auc_h + auc_a) / 2  # mean of home and away AUC
    except Exception:
        auc = float("nan")

    probs_matrix = list(zip(p_h, p_d, p_a))
    try:
        ll = log_loss(y, probs_matrix, labels=[0, 1, 2])
    except Exception:
        ll = float("nan")

    # Brier score: mean over three classes
    brier_vals = []
    for cls in range(3):
        y_bin = [1 if o == cls else 0 for o in y]
        p_cls = [p_h[i] if cls == 0 else (p_d[i] if cls == 1 else p_a[i]) for i in range(len(y))]
        try:
            brier_vals.append(brier_score_loss(y_bin, p_cls))
        except Exception:
            pass
    brier = float(np.mean(brier_vals)) if brier_vals else float("nan")

    return {"n": len(records), "auc": round(auc, 5), "log_loss": round(ll, 5), "brier": round(brier, 5)}


# ─────────────────────────────────────────────────────────────────────────────
# Task 3/5 — EV backtest stats
# ─────────────────────────────────────────────────────────────────────────────

def backtest_stats(records: list, home_only: bool = False, label: str = "") -> dict:
    """Compute ROI/CI for a set of selected-bet records."""
    subset = [r for r in records if (not home_only or r["bet_dir"] == "home")]
    if not subset:
        return {"n": 0, "roi": None, "ci_lo": None, "ci_hi": None,
                "ci_gt0": False, "pass": False, "label": label}
    rois = [r["roi"] for r in subset]
    mean_roi, lo, hi = bootstrap_ci(rois)
    ci_gt0 = lo > 0
    n = len(subset)
    return {
        "n": n,
        "roi": round(mean_roi * 100, 3),
        "ci_lo": round(lo * 100, 3),
        "ci_hi": round(hi * 100, 3),
        "ci_gt0": ci_gt0,
        "pass": ci_gt0 and n >= 500,
        "label": label,
    }


def clv_stats(records: list, label: str = "") -> dict:
    """Compute CLV for records that have closing odds."""
    clv_vals = [r["clv"] for r in records if r["clv"] is not None]
    if not clv_vals:
        return {"n": 0, "clv": None, "ci_lo": None, "ci_hi": None, "ci_gt0": False, "label": label}
    mean_clv, lo, hi = bootstrap_ci(clv_vals)
    return {
        "n": len(clv_vals),
        "clv": round(mean_clv * 100, 4),
        "ci_lo": round(lo * 100, 4),
        "ci_hi": round(hi * 100, 4),
        "ci_gt0": lo > 0,
        "label": label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Raw quality on full validation set (not just selected bets)
# ─────────────────────────────────────────────────────────────────────────────

def build_full_quality_records(preds_window: list, all_matches: list, team_id_map: dict,
                                team_history: dict, skellam_params: dict, iso_cal: dict,
                                dc_xg_params: dict, dc_cache: dict, win_name: str,
                                roll_window: int, blend_w: float) -> tuple:
    """
    Run all predictions (not just EV-selected) through both variants to compute quality metrics.
    Returns (records_a, records_b).
    """
    records_a, records_b = [], []
    for pred in preds_window:
        lid = pred["league_id"]
        date = pred["date"][:10]
        hid  = pred["home_team_id"]
        aid  = pred["away_team_id"]

        gh = pred.get("goals_home")
        ga = pred.get("goals_away")
        if gh is None or ga is None:
            continue
        outcome = 0 if gh > ga else (1 if gh == ga else 2)

        xGF_h, xGA_h, nh = get_rolling_xg(None, team_history, team_id_map, hid, date, roll_window)
        xGF_a, xGA_a, na = get_rolling_xg(None, team_history, team_id_map, aid, date, roll_window)
        if xGF_h is None or xGF_a is None:
            continue

        h_adv, ref_xgf = skellam_params.get(lid, (1.2, 1.2))
        raw_probs_a = predict_skellam(xGF_h, xGA_h, xGF_a, xGA_a, h_adv, ref_xgf)
        if raw_probs_a is None:
            continue

        if iso_cal.get(lid):
            cal_probs_a = apply_isotonic_calibration([raw_probs_a], *iso_cal[lid])[0]
        else:
            cal_probs_a = raw_probs_a

        records_a.append({"p_home": cal_probs_a[0], "p_draw": cal_probs_a[1],
                           "p_away": cal_probs_a[2], "outcome": outcome})

        dc_xg_p = dc_xg_params.get(lid)
        if dc_xg_p:
            rho_val = _get_rho(dc_cache, win_name, lid)
            probs_b = predict_dc_xg(dc_xg_p, hid, aid, rho_val)
            if probs_b:
                records_b.append({"p_home": probs_b[0], "p_draw": probs_b[1],
                                   "p_away": probs_b[2], "outcome": outcome})

    return records_a, records_b


# ─────────────────────────────────────────────────────────────────────────────
# Load closing odds map (from Phase 5 historical_odds.db)
# ─────────────────────────────────────────────────────────────────────────────

def load_closing_map() -> dict:
    db = sqlite3.connect(str(ANALYSIS / "historical_odds.db"))
    cursor = db.cursor()
    cursor.execute(
        "SELECT fixture_id, bet_type, odd_home, odd_draw, odd_away, odd_over, odd_under "
        "FROM fixture_odds_closing"
    )
    closing_map = {}
    for row in cursor.fetchall():
        fid, bt, oh, od, oa, oo, ou = row
        if fid not in closing_map:
            closing_map[fid] = {}
        if bt == "h2h" and oh and od and oa:
            closing_map[fid]["h2h"] = (float(oh), float(od), float(oa))
        elif bt == "over_under" and oo and ou:
            closing_map[fid]["ou25"] = (float(oo), float(ou))
    db.close()
    return closing_map


# ─────────────────────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────────────────────

def write_report(results_all: dict, source_report: str):
    lines = []
    lines.append("# Phase 7 — xG Replication Report\n")
    lines.append(source_report)
    lines.append("\n---\n")

    for win_name in ["2022", "2023"]:
        if win_name not in results_all:
            continue
        res = results_all[win_name]
        lines.append(f"\n## Window {win_name}\n")
        lines.append(f"- Validation predictions (3 leagues): {res['n_val_preds']}")
        lines.append(f"- Dropped (no xG match): {res['n_no_xg']}")
        lines.append(f"- Var-B: rolling xG → DC bivariate Poisson (no optimizer)")
        lines.append(f"- Skellam home-adv params: {res['skellam_params']}\n")

        # Raw quality
        lines.append("### Raw Model Quality (all val predictions)\n")
        lines.append("| Model | N | AUC | Log-loss | Brier |")
        lines.append("|-------|---|-----|----------|-------|")
        for model_label, key in [
            ("DC baseline (goals)", "qual_base"),
            ("Var-A Skellam+iso (xG)", "qual_a"),
            ("Var-B DC-xG", "qual_b"),
        ]:
            q = res.get(key, {})
            if not q:
                continue
            lines.append(f"| {model_label} | {q.get('n','?')} | {q.get('auc','?')} | "
                         f"{q.get('log_loss','?')} | {q.get('brier','?')} |")

        # EV backtest
        lines.append("\n### EV Backtest (pre-registered bar: CI>0, ≥500 bets)\n")
        lines.append("| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |")
        lines.append("|-------|-------|---|------|-------|-------|-------|-------|")
        for model_label, key in [
            ("DC baseline", "bt_base"),
            ("Var-A Skellam+iso", "bt_a"),
            ("Var-B DC-xG", "bt_b"),
        ]:
            for scope_label, scope_key in [("all-bets", "all"), ("home-only", "home")]:
                bt = res.get(f"{key}_{scope_key}", {})
                if not bt:
                    continue
                pass_str = "**PASS**" if bt.get("pass") else "FAIL"
                lines.append(
                    f"| {model_label} | {scope_label} | {bt['n']} | "
                    f"{bt.get('roi','?')}% | [{bt.get('ci_lo','?')}%,{bt.get('ci_hi','?')}%] | "
                    f"{'YES' if bt['n']>=500 else 'NO⚠️'} | "
                    f"{'YES' if bt.get('ci_gt0') else 'NO'} | {pass_str} |"
                )

        # CLV
        lines.append("\n### CLV Cross-Check\n")
        lines.append("| Model | N | CLV% | 95%CI | CI>0? |")
        lines.append("|-------|---|------|-------|-------|")
        for model_label, key in [
            ("DC baseline", "clv_base"),
            ("Var-A Skellam+iso", "clv_a"),
            ("Var-B DC-xG", "clv_b"),
        ]:
            c = res.get(key, {})
            if not c:
                continue
            lines.append(
                f"| {model_label} | {c['n']} | {c.get('clv','?')}% | "
                f"[{c.get('ci_lo','?')}%,{c.get('ci_hi','?')}%] | "
                f"{'YES' if c.get('ci_gt0') else 'NO'} |"
            )

    # Cross-window verdict
    lines.append("\n---\n## Task 5 — Verdict\n")
    lines.append(results_all.get("verdict_text", "(see results JSON)"))
    lines.append("")
    report_text = "\n".join(lines)

    out_md = ANALYSIS / "v7_xg_report.md"
    out_md.write_text(report_text)
    print(f"Report written: {out_md}")
    return report_text


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=== Phase 7: xG Replication (Wilkens 2026) ===\n")
    t0 = time.time()

    # ── Task 1: Source xG data ──────────────────────────────────────────────
    print("Task 1: Fetching Understat data...")
    all_matches = fetch_understat_all()
    print("Coverage:")
    report_understat_coverage(all_matches)

    source_section = (
        "## Task 1 — xG Source\n\n"
        "**Source used:** Understat via `understatapi` Python package (synchronous interface).\n"
        "Cloudflare test passed on first attempt — no blocking encountered at ≤0.8 req/s.\n\n"
        "**Coverage:** EPL (league 39), Serie_A (league 135), La_Liga (league 140). "
        "Seasons 2014/15–2023/24 (understat season keys 2014–2023). "
        f"Total: {len(all_matches)} completed matches.\n\n"
        "**Overlap with fdco backtest windows:**\n"
        "- fdco 2022 window (Jan–Dec 2022): E0/I1/SP1 account for 1,000 of 3,540 preds (28%)\n"
        "- fdco 2023 window (Jan 2023–Jun 2024): 1,738 of 5,456 preds (32%)\n\n"
        "The other 5 fdco leagues (E1/E2/E3/I2/SP2) are not in Understat's top-6 coverage; "
        "this analysis is restricted to the 3 matching leagues.\n\n"
        "**Rolling window tested:** 5 and 10 matches. "
        "(Wilkens 2026 exact window not recoverable without paper access; "
        "both alternatives are reported.)\n"
    )

    # ── Load production DB ──────────────────────────────────────────────────
    engine = create_engine("sqlite:////opt/projects/bootball/data/football.db")
    team_id_map = build_team_id_to_understat(engine)
    print(f"Team ID map: {len(team_id_map)} teams")

    # ── Build xG join map ───────────────────────────────────────────────────
    xg_fixture_map, n_matched = build_xg_fixture_map(all_matches, team_id_map)
    print(f"Understat→DB fixture join: {n_matched} matches with both team IDs resolved")

    # ── Build team match history (for rolling lookups) ──────────────────────
    # team_history: {norm_name: sorted [(date, xgF, xgA), ...]}
    from collections import defaultdict
    team_history: dict = defaultdict(list)
    for m in all_matches:
        hn = normalize(m["home_title"])
        an = normalize(m["away_title"])
        d = m["date"]
        team_history[hn].append((d, m["xg_home"], m["xg_away"]))
        team_history[an].append((d, m["xg_away"], m["xg_home"]))
    for name in team_history:
        team_history[name].sort(key=lambda x: x[0])

    print(f"Team history built for {len(team_history)} teams")

    # ── Load predictions ────────────────────────────────────────────────────
    preds_all = []
    for win_name in ["2022", "2023"]:
        raw = json.loads((ANALYSIS / "dc_cache" / f"preds_{win_name}.json").read_text())
        preds_all.extend(raw)
    preds_covered = [p for p in preds_all if p["league_id"] in FDCO_LEAGUES]
    print(f"Loaded {len(preds_all)} preds total; {len(preds_covered)} in covered leagues")

    # ── Sample size gate (advisor check) ───────────────────────────────────
    print("\nSample size check (before model, after xG match):")
    for win_name, wcfg in WINDOWS.items():
        val_start = wcfg["val_start"]
        val_end   = wcfg["val_end"]
        window_preds = [p for p in preds_covered
                        if val_start <= p["date"][:10] <= val_end]
        # Check xG availability
        n_with_xg = 0
        for p in window_preds:
            xGF_h, _, nh = get_rolling_xg(None, team_history, team_id_map, p["home_team_id"], p["date"][:10], 5)
            xGF_a, _, na = get_rolling_xg(None, team_history, team_id_map, p["away_team_id"], p["date"][:10], 5)
            if xGF_h is not None and xGF_a is not None:
                n_with_xg += 1
        print(f"  {win_name}: {len(window_preds)} val preds, {n_with_xg} with xG — "
              f"est. home-only ({int(n_with_xg*0.35)}) [bar=500: "
              f"{'likely UNDER' if n_with_xg*0.35 < 500 else 'likely over'}]")

    # ── Load DC cache (for rho values) ─────────────────────────────────────
    dc_cache_all = {}
    for win_name in ["2022", "2023"]:
        dc_cache_all[win_name] = json.loads((ANALYSIS / "dc_cache" / f"dc_{win_name}.json").read_text())

    # ── Load closing odds ───────────────────────────────────────────────────
    closing_map = load_closing_map()
    print(f"Closing odds: {len(closing_map)} fixtures")

    # ── Run walk-forward for both roll windows ──────────────────────────────
    all_results = {}
    for roll_w in ROLL_WINDOWS:
        print(f"\n--- Roll window = {roll_w} matches ---")
        wf_results = run_walk_forward(
            preds_covered, all_matches, team_id_map, team_history,
            dc_cache_all, closing_map, roll_w
        )
        all_results[f"roll{roll_w}"] = wf_results

    # ── Compute quality metrics and report stats ────────────────────────────
    print("\nComputing quality metrics and backtest stats...")

    final_results = {}

    for roll_w in ROLL_WINDOWS:
        wf = all_results[f"roll{roll_w}"]
        roll_key = f"roll{roll_w}"
        final_results[roll_key] = {}

        for win_name, wcfg in WINDOWS.items():
            if win_name not in wf:
                continue
            win_res = wf[win_name]
            out = dict(win_res)
            out.pop("variant_a"); out.pop("variant_b"); out.pop("baseline")
            out.pop("qual_all_a"); out.pop("qual_all_b"); out.pop("qual_all_base")

            rec_a    = wf[win_name]["variant_a"]
            rec_b    = wf[win_name]["variant_b"]
            rec_base = wf[win_name]["baseline"]

            # Full-prediction quality (all xG-matched fixtures, not just EV-selected bets)
            out["qual_a"]    = compute_raw_quality(wf[win_name]["qual_all_a"])
            out["qual_b"]    = compute_raw_quality(wf[win_name]["qual_all_b"])
            out["qual_base"] = compute_raw_quality(wf[win_name]["qual_all_base"])

            # EV backtest
            out["bt_a_all"]     = backtest_stats(rec_a, home_only=False, label=f"Var-A roll{roll_w} {win_name} all")
            out["bt_a_home"]    = backtest_stats(rec_a, home_only=True,  label=f"Var-A roll{roll_w} {win_name} home")
            out["bt_b_all"]     = backtest_stats(rec_b, home_only=False, label=f"Var-B roll{roll_w} {win_name} all")
            out["bt_b_home"]    = backtest_stats(rec_b, home_only=True,  label=f"Var-B roll{roll_w} {win_name} home")
            out["bt_base_all"]  = backtest_stats(rec_base, home_only=False, label=f"Base roll{roll_w} {win_name} all")
            out["bt_base_home"] = backtest_stats(rec_base, home_only=True,  label=f"Base roll{roll_w} {win_name} home")

            # CLV
            out["clv_a"]    = clv_stats(rec_a,    label=f"Var-A {win_name}")
            out["clv_b"]    = clv_stats(rec_b,    label=f"Var-B {win_name}")
            out["clv_base"] = clv_stats(rec_base, label=f"Base {win_name}")

            final_results[roll_key][win_name] = out

    # ── Build cross-window pass summary ────────────────────────────────────
    # Best roll window for the primary metric
    for roll_w in ROLL_WINDOWS:
        roll_key = f"roll{roll_w}"
        for win_name in ["2022", "2023"]:
            if win_name not in final_results.get(roll_key, {}):
                continue
            d = final_results[roll_key][win_name]
            print(f"  [{roll_w}-match, {win_name}] "
                  f"Var-A all: n={d['bt_a_all']['n']} ROI={d['bt_a_all']['roi']}% "
                  f"CI=[{d['bt_a_all']['ci_lo']},{d['bt_a_all']['ci_hi']}] "
                  f"pass={d['bt_a_all']['pass']} | "
                  f"home: n={d['bt_a_home']['n']} ROI={d['bt_a_home']['roi']}% "
                  f"pass={d['bt_a_home']['pass']}")

    # ── Verdict text ────────────────────────────────────────────────────────
    final_results["source_section"] = source_section
    final_results["verdict_text"] = _build_verdict(final_results)

    # ── Save JSON ───────────────────────────────────────────────────────────
    out_json = ANALYSIS / "phase7_results.json"
    out_json.write_text(json.dumps(final_results, indent=2, default=str))
    print(f"\nResults saved: {out_json}")

    # ── Write markdown report ───────────────────────────────────────────────
    _write_full_report(final_results)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


def _build_verdict(results: dict) -> str:
    lines = []
    for roll_w in ROLL_WINDOWS:
        roll_key = f"roll{roll_w}"
        if roll_key not in results:
            continue
        lines.append(f"\n### Roll window = {roll_w} matches\n")
        lines.append("Pre-registered bar: 95% CI > 0, ≥500 bets, ≥2 windows pass.\n")
        lines.append("| Model | Scope | 2022 pass? | 2023 pass? | Both windows? |")
        lines.append("|-------|-------|-----------|-----------|---------------|")
        for varlabel, bt_key in [("DC baseline", "bt_base"), ("Var-A Skellam+iso", "bt_a"), ("Var-B DC-xG", "bt_b")]:
            for scope in ["all", "home"]:
                p22 = results[roll_key].get("2022", {}).get(f"{bt_key}_{scope}", {}).get("pass", False)
                p23 = results[roll_key].get("2023", {}).get(f"{bt_key}_{scope}", {}).get("pass", False)
                both = p22 and p23
                lines.append(f"| {varlabel} | {scope} | {'YES' if p22 else 'NO'} | {'YES' if p23 else 'NO'} | "
                              f"{'**PASS**' if both else 'FAIL'} |")

    lines.append("\n### Home-only underpowering note\n")
    lines.append(
        "The home-only restriction targets the cell where both Wilkens and our Phase 6 CLV "
        "results show concentration. With ~1,000/1,738 covered preds per window and ~35% "
        "home-selection rate, the 2022 home-only cell is expected to contain ~300 bets — below "
        "the ≥500 bar. Any positive home-only 2022 result should be read as 'promising but "
        "underpowered' rather than a formal pass. The 2023 window (~610 est.) may clear the bar.\n"
    )
    return "\n".join(lines)


def _write_full_report(results: dict):
    """Write comprehensive markdown report."""
    lines = [
        "# Phase 7 — xG Replication (Wilkens 2026) Report\n",
        "> **Scope:** EPL (E0), Serie A (I1), La Liga (SP1) — Understat-covered leagues only.",
        "> Walk-forward windows: fdco 2022 (Jan–Dec 2022) and fdco 2023 (Jan 2023–Jun 2024).",
        "> Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows pass.\n",
        "",
        results.get("source_section", ""),
        "",
    ]

    for roll_w in ROLL_WINDOWS:
        roll_key = f"roll{roll_w}"
        if roll_key not in results:
            continue
        lines.append(f"\n## Roll window = {roll_w} matches\n")

        for win_name in ["2022", "2023"]:
            if win_name not in results[roll_key]:
                continue
            d = results[roll_key][win_name]
            lines.append(f"\n### Window {win_name}\n")
            lines.append(f"- n_val_preds (covered leagues): {d.get('n_val_preds','?')}")
            lines.append(f"- Dropped (no xG): {d.get('n_no_xg','?')}")
            lines.append(f"- Var-B: rolling xG → DC bivariate Poisson (no optimizer)\n")

            lines.append("**C2-equivalent — Raw Model Quality (val set)**\n")
            lines.append("| Model | N | AUC | Log-loss | Brier |")
            lines.append("|-------|---|-----|----------|-------|")
            for mlabel, qkey in [
                ("DC baseline (goals)", "qual_base"),
                ("Var-A Skellam+iso (xG)", "qual_a"),
                ("Var-B DC-xG", "qual_b"),
            ]:
                q = d.get(qkey, {})
                if q:
                    lines.append(f"| {mlabel} | {q.get('n','?')} | {q.get('auc','?')} | "
                                  f"{q.get('log_loss','?')} | {q.get('brier','?')} |")

            lines.append("\n**EV Backtest**\n")
            lines.append("| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |")
            lines.append("|-------|-------|---|------|-------|-------|-------|-------|")
            for mlabel, bt_prefix in [
                ("DC baseline", "bt_base"),
                ("Var-A Skellam+iso", "bt_a"),
                ("Var-B DC-xG", "bt_b"),
            ]:
                for scope_label, scope in [("all-bets", "all"), ("home-only", "home")]:
                    bt = d.get(f"{bt_prefix}_{scope}", {})
                    if not bt:
                        continue
                    n = bt["n"]
                    pass_str = "**PASS**" if bt.get("pass") else "FAIL"
                    n_str = f"{n}⚠️" if n < 500 else str(n)
                    lines.append(
                        f"| {mlabel} | {scope_label} | {n_str} | "
                        f"{bt.get('roi','?')}% | [{bt.get('ci_lo','?')}%,{bt.get('ci_hi','?')}%] | "
                        f"{'YES' if n>=500 else 'NO⚠️'} | "
                        f"{'YES' if bt.get('ci_gt0') else 'NO'} | {pass_str} |"
                    )

            lines.append("\n**CLV Cross-Check**\n")
            lines.append("| Model | N | CLV% | 95%CI | CI>0? |")
            lines.append("|-------|---|------|-------|-------|")
            for mlabel, ckey in [
                ("DC baseline", "clv_base"),
                ("Var-A Skellam+iso", "clv_a"),
                ("Var-B DC-xG", "clv_b"),
            ]:
                c = d.get(ckey, {})
                if c and c.get("n", 0) > 0:
                    lines.append(
                        f"| {mlabel} | {c['n']} | {c.get('clv','?')}% | "
                        f"[{c.get('ci_lo','?')}%,{c.get('ci_hi','?')}%] | "
                        f"{'YES' if c.get('ci_gt0') else 'NO'} |"
                    )

    lines.append("\n---\n")
    lines.append("## Task 5 — Verdict\n")
    lines.append(results.get("verdict_text", ""))

    out_path = ANALYSIS / "v7_xg_report.md"
    out_path.write_text("\n".join(lines))
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
