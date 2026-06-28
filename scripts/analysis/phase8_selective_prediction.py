#!/usr/bin/env python3
"""Phase 8 — Selective Prediction / Calibrated Abstention.

Tests whether abstaining on low-reliability predictions converts existing
CLV signal (+2% CI>0) into realized edge, by shrinking the selection penalty
identified in Phase 6/7 as the binding constraint.

Walk-forward discipline:
  2022 test window — conformal threshold calibrated on pre-2022 fdco training bets.
  2023 test window — conformal threshold calibrated on 2022 validation bets.
  Abstention rule sees NO held-out outcomes at calibration time.

Pre-registered stopping rule: Section 5 of Phase 8 brief (AUDIT_V2.md).
"""

import csv, json, math, sys, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, text

# ── Phase 7 import ────────────────────────────────────────────────────────────
ANALYSIS = Path(__file__).parent
ROOT = ANALYSIS.parent.parent

import importlib.util
_spec = importlib.util.spec_from_file_location("phase7", ANALYSIS / "phase7_xg_analysis.py")
p7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p7)

# ── Constants ─────────────────────────────────────────────────────────────────
FDCO_LEAGUE_MAP = {"E0": 39, "I1": 135, "SP1": 140}  # xG-covered leagues only
FDCO_LEAGUES    = [39, 135, 140]
WINDOWS = {
    "2022": {"val_start": "2022-01-01", "val_end": "2022-12-31", "blend_w": 1.0},
    "2023": {"val_start": "2023-01-01", "val_end": "2024-06-30", "blend_w": 0.65},
}
ROLL_WINDOW  = 10          # Var-B roll=10 is the Phase 7 primary model
BOT_MIN_EV   = 0.05        # EV threshold (matches production)
B365_MARGIN  = 0.055       # Approximate B365 h2h overround
N_BOOTSTRAP  = 5000
RNG          = np.random.default_rng(42)

# Abstention nominal rates (fraction of bets to DROP from least confident end)
ABSTENTION_RATES = [0.0, 0.25, 0.50, 0.75]

# Pre-2022 training seasons for calibrating the 2022 window
TRAIN_SEASONS_2022 = ["1920", "2021", "2122"]

# ── fdco team-name → Understat team-name (lowercase, as normalize() returns) ──
FDCO_ALIASES = {
    # EPL
    "man city":           "manchester city",
    "man united":         "manchester united",
    "nott'm forest":      "nottingham forest",
    "wolves":             "wolverhampton wanderers",
    "west brom":          "west bromwich albion",
    "newcastle":          "newcastle united",
    # Serie A
    "milan":              "ac milan",
    "spal":               "spal 2013",
    # La Liga
    "ath bilbao":         "athletic club",
    "ath madrid":         "atletico madrid",
    "sociedad":           "real sociedad",
    "celta":              "celta vigo",
    "espanol":            "espanyol",
    "betis":              "real betis",
    "vallecano":          "rayo vallecano",
    "huesca":             "sd huesca",
    "valladolid":         "real valladolid",
}


def fdco_normalize(name: str) -> str:
    n = name.lower().strip()
    return FDCO_ALIASES.get(n, n)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap helpers (same seed as Phase 7)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_ci(values, n=N_BOOTSTRAP, seed=42):
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return float("nan"), float("nan"), float("nan")
    rng_b = np.random.default_rng(seed)
    means = [np.mean(rng_b.choice(arr, size=len(arr), replace=True)) for _ in range(n)]
    return float(np.mean(arr)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def bet_stats(records, label=""):
    if not records:
        return {"n": 0, "roi": None, "ci_lo": None, "ci_hi": None,
                "ci_gt0": False, "pass_bar": False, "label": label}
    rois = [r["roi"] for r in records]
    mean_roi, lo, hi = bootstrap_ci(rois)
    ci_gt0 = lo > 0
    n = len(records)
    return {
        "n": n,
        "roi": round(mean_roi * 100, 3),
        "ci_lo": round(lo * 100, 3),
        "ci_hi": round(hi * 100, 3),
        "ci_gt0": ci_gt0,
        "pass_bar": ci_gt0 and n >= 500,
        "label": label,
    }


def clv_stats(records, clv_key="clv", label=""):
    vals = [r[clv_key] for r in records if r.get(clv_key) is not None]
    if not vals:
        return {"n": 0, "clv": None, "ci_lo": None, "ci_hi": None, "ci_gt0": False, "label": label}
    mean_c, lo, hi = bootstrap_ci(vals)
    return {
        "n": len(vals),
        "clv": round(mean_c * 100, 4),
        "ci_lo": round(lo * 100, 4),
        "ci_hi": round(hi * 100, 4),
        "ci_gt0": lo > 0,
        "label": label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Pinnacle closing-line map
# ─────────────────────────────────────────────────────────────────────────────

def build_pinnacle_closing_map(fdco_cache: Path) -> dict:
    """Return {(league_id, date_iso, b365h_r2, b365a_r2): (psch, pscd, psca)}.

    Uses B365H and B365A (rounded to 2dp) as a composite join key alongside
    date and league.  Collisions within a matchday are extremely rare but
    checked post-build.
    """
    result = {}
    dupes = 0
    for code, lid in FDCO_LEAGUE_MAP.items():
        for season in ["1920", "2021", "2122", "2223", "2324"]:
            fpath = fdco_cache / f"{code}_{season}.csv"
            if not fpath.exists():
                continue
            with open(fpath, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        d = datetime.strptime(row["Date"], "%d/%m/%Y").strftime("%Y-%m-%d")
                        b365h = float(row["B365H"])
                        b365d = float(row["B365D"])
                        b365a = float(row["B365A"])
                        psch  = float(row["PSCH"])
                        pscd  = float(row["PSCD"])
                        psca  = float(row["PSCA"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if any(o <= 1.0 for o in [b365h, b365d, b365a, psch, pscd, psca]):
                        continue
                    key = (lid, d, round(b365h, 2), round(b365a, 2))
                    if key in result:
                        dupes += 1
                    else:
                        result[key] = (psch, pscd, psca, round(b365d, 2))
    print(f"Pinnacle closing map: {len(result)} entries, {dupes} key collisions (should be 0)")
    return result


def lookup_pinnacle(pin_map: dict, rec: dict) -> tuple:
    """Return (psch, pscd, psca) for a bet record, or None if no match."""
    key = (rec["league_id"], rec["date"],
           round(rec["bet_odds"] if rec["bet_dir"] == "home" else rec.get("_b365h", 0), 2),
           round(rec.get("_b365a", 0), 2))
    return pin_map.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Add Pinnacle CLV to bet records
# ─────────────────────────────────────────────────────────────────────────────

def enrich_with_pinnacle(bets: list, pin_map: dict, preds_by_id: dict) -> list:
    """Attach pin_clv and b365_close_clv to each bet record (in-place return)."""
    enriched = []
    no_match = 0
    for rec in bets:
        r = dict(rec)
        # Find full opening odds from preds cache for join key construction
        pred = preds_by_id.get(r.get("fixture_id"))
        if pred:
            b365h = pred.get("odd_home")
            b365a = pred.get("odd_away")
            b365d = pred.get("odd_draw")
        else:
            b365h = b365a = b365d = None

        pin_entry = None
        if b365h and b365a:
            key = (r["league_id"], r["date"], round(b365h, 2), round(b365a, 2))
            pin_entry = pin_map.get(key)

        if pin_entry:
            psch, pscd, psca, _ = pin_entry
            pin_odds = [psch, pscd, psca]
            bet_idx = ["home", "draw", "away"].index(r["bet_dir"])
            pin_close = pin_odds[bet_idx]
            if pin_close > 1.0:
                r["pin_clv"] = (r["bet_odds"] - pin_close) / pin_close
            else:
                r["pin_clv"] = None
        else:
            r["pin_clv"] = None
            no_match += 1

        enriched.append(r)

    coverage = 1.0 - no_match / max(len(bets), 1)
    print(f"  Pinnacle join: {len(bets) - no_match}/{len(bets)} matched "
          f"({coverage:.1%} coverage)")
    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Build pre-2022 training bets from fdco CSVs
# ─────────────────────────────────────────────────────────────────────────────

def build_pre_2022_training_bets(
    fdco_cache: Path,
    team_id_map: dict,
    team_history: dict,
    skellam_params: dict,    # {league_id: (home_adv, ref_xgf)} from 2022 window training
    dc_rho: dict,            # {league_id: rho} from dc_cache_2022
    blend_w: float = 1.0,
) -> list:
    """Build training bets from fdco rows dated before 2022-01-01.

    Uses the skellam_params fitted on pre-2022 Understat data (stored in
    phase7_results.json for the 2022 window) plus the DC rho values from
    dc_2022.json.  Blend weight = 1.0 (2022-window default).
    """
    norm_to_id = {v: k for k, v in team_id_map.items()}
    bets = []
    matched_teams  = 0
    skipped_teams  = 0
    skipped_xg     = 0
    skipped_odds   = 0
    skipped_ev     = 0

    for code, lid in FDCO_LEAGUE_MAP.items():
        h_adv, ref_xgf = skellam_params.get(lid, (1.2, 1.2))
        rho = dc_rho.get(lid, 0.0)

        for season in TRAIN_SEASONS_2022:
            fpath = fdco_cache / f"{code}_{season}.csv"
            if not fpath.exists():
                continue
            with open(fpath, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        date = datetime.strptime(row["Date"], "%d/%m/%Y").strftime("%Y-%m-%d")
                    except (ValueError, KeyError):
                        continue
                    if date >= "2022-01-01":
                        continue  # only training period

                    try:
                        b365h = float(row["B365H"])
                        b365d = float(row["B365D"])
                        b365a = float(row["B365A"])
                        goals_h = int(row["FTHG"])
                        goals_a = int(row["FTAG"])
                    except (ValueError, KeyError, TypeError):
                        skipped_odds += 1
                        continue

                    if any(o <= 1.0 for o in [b365h, b365d, b365a]):
                        skipped_odds += 1
                        continue

                    home_norm = fdco_normalize(row.get("HomeTeam", ""))
                    away_norm = fdco_normalize(row.get("AwayTeam", ""))
                    hid = norm_to_id.get(home_norm)
                    aid = norm_to_id.get(away_norm)
                    if hid is None or aid is None:
                        skipped_teams += 1
                        continue
                    matched_teams += 1

                    xGF_h, xGA_h, nh = p7.get_rolling_xg(
                        None, team_history, team_id_map, hid, date, ROLL_WINDOW)
                    xGF_a, xGA_a, na = p7.get_rolling_xg(
                        None, team_history, team_id_map, aid, date, ROLL_WINDOW)
                    if xGF_h is None or xGF_a is None:
                        skipped_xg += 1
                        continue

                    probs_b = p7.predict_dc_rolling_xg(
                        xGF_h, xGA_h, xGF_a, xGA_a, h_adv, ref_xgf, rho)
                    if probs_b is None:
                        skipped_xg += 1
                        continue

                    open_odds = (b365h, b365d, b365a)
                    ev, pb = p7.blend_ev(probs_b[:3], open_odds, blend_w)
                    bet = p7.select_bet(ev, open_odds)
                    if bet is None:
                        skipped_ev += 1
                        continue

                    bet_idx, bet_dir, bet_odds = bet
                    outcome = 0 if goals_h > goals_a else (1 if goals_h == goals_a else 2)
                    won = (outcome == bet_idx)

                    shin_p = p7.shin_probabilities(list(open_odds))
                    p_market_selected = shin_p[bet_idx]

                    bets.append({
                        "league_id":     lid,
                        "date":          date,
                        "bet_dir":       bet_dir,
                        "bet_odds":      bet_odds,
                        "bet_idx":       bet_idx,
                        "outcome":       outcome,
                        "won":           won,
                        "roi":           (bet_odds - 1.0) if won else -1.0,
                        "p_home":        probs_b[0],
                        "p_draw":        probs_b[1],
                        "p_away":        probs_b[2],
                        "p_selected":    probs_b[bet_idx],
                        "p_market":      p_market_selected,
                        "disagreement":  abs(probs_b[bet_idx] - p_market_selected),
                        "ev":            ev[bet_idx],
                        "n_xg_home":     nh,
                        "n_xg_away":     na,
                    })

    print(f"  Pre-2022 training bets: {len(bets)} bets")
    print(f"    team matched={matched_teams}, skipped_teams={skipped_teams}, "
          f"skipped_xg={skipped_xg}, skipped_ev={skipped_ev}")
    return bets


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Augment validation bets with p_selected, disagreement
# ─────────────────────────────────────────────────────────────────────────────

def augment_bets(records: list, preds_by_id: dict) -> list:
    """Add p_selected, p_market, disagreement fields to each bet record."""
    out = []
    for r in records:
        rec = dict(r)
        bet_dir = rec["bet_dir"]
        bet_idx = ["home", "draw", "away"].index(bet_dir)
        rec["p_selected"] = [rec["p_home"], rec["p_draw"], rec["p_away"]][bet_idx]
        rec["bet_idx"] = bet_idx

        # Market probability from opening odds (B365H/D/A in preds_all)
        pred = preds_by_id.get(rec.get("fixture_id"))
        if pred:
            open_odds = (pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away"))
            if all(o and o > 1.0 for o in open_odds):
                shin_p = p7.shin_probabilities(list(open_odds))
                rec["p_market"] = shin_p[bet_idx]
                rec["disagreement"] = abs(rec["p_selected"] - shin_p[bet_idx])
                rec["_b365h"] = open_odds[0]
                rec["_b365d"] = open_odds[1]
                rec["_b365a"] = open_odds[2]
                # Per-bet overround for margin calculation
                total_imp = sum(1.0 / o for o in open_odds)
                rec["b365_overround"] = total_imp  # typically 1.05–1.07
            else:
                rec["p_market"] = rec["disagreement"] = None
                rec["_b365h"] = rec["_b365a"] = rec["_b365d"] = None
                rec["b365_overround"] = 1.055
        else:
            rec["p_market"] = rec["disagreement"] = None
            rec["_b365h"] = rec["_b365a"] = rec["_b365d"] = None
            rec["b365_overround"] = 1.055

        # Per-bet implied margin: margin = overround / (overround + (overround-1)) ≈ 1 - 1/overround
        rec["implied_margin"] = 1.0 - 1.0 / rec["b365_overround"]

        out.append(rec)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Task 1: Reliability signal inventory
# ─────────────────────────────────────────────────────────────────────────────

def decile_analysis(bets: list, signal_key: str, n_deciles: int = 10) -> list:
    """Bucket bets by decile of signal_key; return per-decile stats.

    Uses bets' OWN distribution for decile edges (pure signal bucketing, no
    test-set leakage since deciles are based on signal, not outcomes).
    """
    eligible = [b for b in bets if b.get(signal_key) is not None]
    if not eligible:
        return []
    signal_vals = np.array([b[signal_key] for b in eligible])
    edges = np.percentile(signal_vals, np.linspace(0, 100, n_deciles + 1))

    rows = []
    for i in range(n_deciles):
        lo_edge = edges[i]
        hi_edge = edges[i + 1]
        in_bucket = [
            b for b in eligible
            if (lo_edge <= b[signal_key] <= hi_edge
                if i == n_deciles - 1
                else lo_edge <= b[signal_key] < hi_edge)
        ]
        if not in_bucket:
            continue
        rois = [b["roi"] for b in in_bucket]
        clv_vals = [b["clv"] for b in in_bucket if b.get("clv") is not None]
        mean_margin = np.mean([b.get("implied_margin", 0.055) for b in in_bucket])
        mean_roi, roi_lo, roi_hi = bootstrap_ci(rois) if rois else (float("nan"),) * 3
        mean_clv = np.mean(clv_vals) if clv_vals else float("nan")
        sel_penalty = mean_clv - mean_margin - mean_roi if not math.isnan(mean_clv) else float("nan")
        rows.append({
            "decile": i + 1,
            "signal_lo": float(round(lo_edge, 4)),
            "signal_hi": float(round(hi_edge, 4)),
            "n": len(in_bucket),
            "n_clv": len(clv_vals),
            "roi": round(float(mean_roi) * 100, 3),
            "roi_lo": round(float(roi_lo) * 100, 3),
            "roi_hi": round(float(roi_hi) * 100, 3),
            "clv": round(float(mean_clv) * 100, 4) if not math.isnan(mean_clv) else None,
            "margin": round(float(mean_margin) * 100, 3),
            "sel_penalty": round(float(sel_penalty) * 100, 3) if not math.isnan(sel_penalty) else None,
        })
    return rows


def monotonicity_check(decile_rows: list, metric: str = "roi") -> dict:
    """Test whether metric is monotonically improving across deciles."""
    vals = [r[metric] for r in decile_rows if r.get(metric) is not None]
    if len(vals) < 3:
        return {"monotone": False, "direction": None, "n_deciles": len(vals)}
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    n_pos = sum(1 for d in diffs if d > 0)
    n_neg = sum(1 for d in diffs if d < 0)
    direction = "increasing" if n_pos >= len(diffs) * 0.7 else (
        "decreasing" if n_neg >= len(diffs) * 0.7 else "none")
    return {
        "monotone": direction in ("increasing", "decreasing"),
        "direction": direction,
        "n_increasing": n_pos,
        "n_decreasing": n_neg,
        "n_deciles": len(vals),
    }


def task1_signals(bets_win: list, win_name: str) -> dict:
    """Full reliability signal inventory for one validation window."""
    print(f"\n  Task 1 — {win_name}: {len(bets_win)} bets")

    out = {"window": win_name, "n_bets": len(bets_win)}

    # Signal 1: p_selected (model confidence in bet direction)
    print(f"    Signal 1: p_selected decile analysis")
    dec_p = decile_analysis(bets_win, "p_selected")
    mono_p = monotonicity_check(dec_p, "roi")
    out["signal_p_selected"] = {
        "deciles": dec_p,
        "monotonicity": mono_p,
        "interpretation": (
            f"p_selected {'increases' if mono_p['direction']=='increasing' else 'does NOT monotonically increase'} "
            f"ROI across deciles ({mono_p['n_increasing']}/{mono_p['n_deciles']-1} steps positive)"
        ),
    }

    # Signal 2: model-market disagreement (|p_model - p_market|)
    print(f"    Signal 2: disagreement decile analysis")
    eligible_dis = [b for b in bets_win if b.get("disagreement") is not None]
    if eligible_dis:
        dec_d = decile_analysis(eligible_dis, "disagreement")
        mono_d = monotonicity_check(dec_d, "roi")
        out["signal_disagreement"] = {"deciles": dec_d, "monotonicity": mono_d}
    else:
        out["signal_disagreement"] = {"deciles": [], "monotonicity": {}}

    # Signal 3: xG data depth (min of n_xg_home, n_xg_away)
    print(f"    Signal 3: xG data depth")
    for b in bets_win:
        nh = b.get("n_xg_home", 0) or 0
        na = b.get("n_xg_away", 0) or 0
        b["xg_depth"] = min(nh, na)
    dec_xg = decile_analysis(bets_win, "xg_depth", n_deciles=5)
    mono_xg = monotonicity_check(dec_xg, "roi")
    out["signal_xg_depth"] = {"deciles": dec_xg, "monotonicity": mono_xg}

    # Signal 4: league-specific breakdown
    print(f"    Signal 4: per-league")
    league_results = {}
    for lid in FDCO_LEAGUES:
        lb = [b for b in bets_win if b["league_id"] == lid]
        if lb:
            league_results[str(lid)] = bet_stats(lb, label=f"league_{lid}_{win_name}")
    out["signal_league"] = league_results

    # EV bucket analysis
    print(f"    Signal 5: EV bucket")
    dec_ev = decile_analysis(bets_win, "ev", n_deciles=5)
    mono_ev = monotonicity_check(dec_ev, "roi")
    out["signal_ev"] = {"deciles": dec_ev, "monotonicity": mono_ev}

    # Summary: which signal shows strongest monotone ROI relationship?
    best_signal = max(
        [
            ("p_selected", mono_p.get("n_increasing", 0)),
            ("disagreement", mono_d.get("n_increasing", 0) if eligible_dis else 0),
            ("xg_depth", mono_xg.get("n_increasing", 0)),
            ("ev", mono_ev.get("n_increasing", 0)),
        ],
        key=lambda x: x[1],
    )
    out["best_monotone_signal"] = best_signal[0]
    out["best_monotone_n_increasing"] = best_signal[1]
    print(f"    → Best monotone signal: {best_signal[0]} "
          f"({best_signal[1]}/{mono_p['n_deciles']-1} steps)")

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Task 2: Conformal / threshold abstention layer
# ─────────────────────────────────────────────────────────────────────────────

def calibrate_threshold(train_bets: list, abstention_rate: float,
                        signal_key: str = "p_selected") -> float:
    """Fit abstention threshold on training bets.

    Returns p_threshold such that (1-abstention_rate) of training bets have
    signal_key >= threshold.  Higher p_selected = more confident = keep.

    abstention_rate=0.25 → keep top 75% most confident.
    """
    vals = np.array([b[signal_key] for b in train_bets if b.get(signal_key) is not None])
    if len(vals) == 0:
        return 0.0
    # Threshold = (abstention_rate * 100)th percentile → keep everything above
    return float(np.percentile(vals, abstention_rate * 100))


def apply_abstention(bets: list, threshold: float,
                     signal_key: str = "p_selected",
                     direction: str = "above") -> list:
    """Keep bets where signal_key >= threshold (or <= if direction='below')."""
    if direction == "above":
        return [b for b in bets if b.get(signal_key, -1) >= threshold]
    else:
        return [b for b in bets if b.get(signal_key, 999) <= threshold]


def task2_abstention(
    bets_2022: list, bets_2023: list,
    train_bets_2022: list,     # pre-2022 fdco training bets
    task1_results: dict,
) -> dict:
    """Selective prediction layer with walk-forward-safe calibration.

    2022 window: threshold calibrated on pre-2022 training bets.
    2023 window: threshold calibrated on 2022 validation bets.
    """
    results = {}

    # Select the abstention signal from Task 1
    # Use p_selected as primary per brief instructions (first and most diagnostic)
    # but also report disagreement for comparison
    primary_signal = "p_selected"

    window_configs = [
        ("2022", bets_2022, train_bets_2022, "pre-2022 fdco training bets"),
        ("2023", bets_2023, bets_2022,       "2022 validation bets"),
    ]

    for win_name, test_bets, calibration_bets, cal_label in window_configs:
        print(f"\n  Task 2 — {win_name}: {len(test_bets)} test bets, "
              f"{len(calibration_bets)} calibration bets ({cal_label})")

        if not calibration_bets:
            print(f"    WARNING: No calibration bets for {win_name}; skipping")
            results[win_name] = {}
            continue

        win_res = {"calibration_n": len(calibration_bets), "rows": []}

        for abs_rate in ABSTENTION_RATES:
            if abs_rate == 0.0:
                # Full set — no abstention
                selective = test_bets
                tau = float("-inf")
            else:
                tau = calibrate_threshold(calibration_bets, abs_rate, primary_signal)
                selective = apply_abstention(test_bets, tau, primary_signal, "above")

            if not selective:
                win_res["rows"].append({"abstention_rate": abs_rate, "n_bets": 0})
                continue

            roi_s = bet_stats(selective, label=f"{win_name}_abs{int(abs_rate*100)}")
            clv_b365 = clv_stats(selective, "clv",     label="b365_close")
            clv_pin  = clv_stats(selective, "pin_clv", label="pinnacle_close")

            # Mean CLV and margin for selection penalty
            clv_vals = [b["clv"] for b in selective if b.get("clv") is not None]
            mean_margins = [b.get("implied_margin", 0.055) for b in selective]
            mean_clv = np.mean(clv_vals) if clv_vals else float("nan")
            mean_margin = np.mean(mean_margins)
            mean_roi_raw = np.mean([b["roi"] for b in selective])
            sel_penalty = (mean_clv - mean_margin - mean_roi_raw
                           if not math.isnan(mean_clv) else float("nan"))

            # Nominal vs actual abstention rate
            actual_abs_rate = 1.0 - len(selective) / max(len(test_bets), 1)

            row = {
                "abstention_rate_nominal": abs_rate,
                "abstention_rate_actual": round(actual_abs_rate, 3),
                "threshold": round(tau, 4) if tau != float("-inf") else None,
                "n_bets": len(selective),
                "roi": roi_s,
                "clv_b365": clv_b365,
                "clv_pinnacle": clv_pin,
                "selection_penalty_pct": round(float(sel_penalty) * 100, 3)
                    if not math.isnan(sel_penalty) else None,
            }
            win_res["rows"].append(row)

            prefix = f"    abs={abs_rate:.0%}"
            print(f"{prefix}: n={len(selective):4d} τ={tau:.3f}  "
                  f"ROI={roi_s['roi']}% [{roi_s['ci_lo']},{roi_s['ci_hi']}]  "
                  f"CLV_B365={clv_b365['clv']}%  CLV_PIN={clv_pin['clv']}%  "
                  f"SP={row['selection_penalty_pct']}%")

        results[win_name] = win_res

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Task 4: Stopping rule
# ─────────────────────────────────────────────────────────────────────────────

def task4_stopping_rule(task2: dict) -> dict:
    """Apply pre-registered Phase 8 stopping rule (Section 5 of AUDIT_V2.md).

    Continue prediction-side work if:
      (A) ROI CI upper > 0 vs Pinnacle in ≥1 window, AND
      (B) ROI point estimate better in both windows vs baseline, AND
      (C) ≥500 bets in selective set, AND
      (D) Selection penalty < 4pp in both windows.

    Pivot to market-structure if:
      Improved ROI but <500 bets/window OR Pinnacle CLV ≤ 0.

    Stop entirely if:
      Abstention does NOT monotonically improve ROI (penalty is diffuse).
    """
    # Find the best (lowest abstention) row that improves ROI vs baseline (0% abstention)
    verdict = {}
    window_verdicts = {}

    for win_name in ["2022", "2023"]:
        win = task2.get(win_name, {})
        rows = win.get("rows", [])
        if not rows:
            window_verdicts[win_name] = "NO_DATA"
            continue

        baseline_row = next((r for r in rows if r["abstention_rate_nominal"] == 0.0), None)
        if not baseline_row:
            window_verdicts[win_name] = "NO_BASELINE"
            continue

        base_roi = baseline_row["roi"]["roi"] if baseline_row.get("roi") else None
        base_n   = baseline_row["n_bets"]

        # Check if any selective level improves both ROI and meets criteria
        best_level = None
        for row in rows:
            if row["abstention_rate_nominal"] == 0.0:
                continue
            roi_r = row.get("roi", {})
            clv_r = row.get("clv_pinnacle", {})
            n     = row.get("n_bets", 0)
            sp    = row.get("selection_penalty_pct")

            if roi_r and base_roi is not None:
                improved = (roi_r["roi"] is not None and
                            roi_r["roi"] > base_roi)
                ci_upper_pos = (roi_r.get("ci_hi", -999) or -999) > 0
                pin_clv_pos  = (clv_r.get("ci_gt0") if clv_r else False)
                meets_n      = n >= 500
                meets_sp     = (sp is not None and abs(sp) < 4.0)

                if improved and best_level is None:
                    best_level = {
                        "abstention_rate": row["abstention_rate_nominal"],
                        "n": n,
                        "roi": roi_r.get("roi"),
                        "ci_hi": roi_r.get("ci_hi"),
                        "pin_clv_ci_gt0": pin_clv_pos,
                        "meets_n": meets_n,
                        "meets_sp": meets_sp,
                        "ci_upper_pos": ci_upper_pos,
                    }

        window_verdicts[win_name] = best_level

    # Evaluate stopping rule across windows
    w22 = window_verdicts.get("2022") or {}
    w23 = window_verdicts.get("2023") or {}

    # Check monotonicity: does ROI improve as abstention rises?
    def check_monotone(win_name):
        rows = task2.get(win_name, {}).get("rows", [])
        rois = [r["roi"]["roi"] for r in rows
                if isinstance(r.get("roi"), dict) and r["roi"].get("roi") is not None]
        if len(rois) < 2:
            return False
        return sum(1 for i in range(len(rois)-1) if rois[i+1] > rois[i]) >= len(rois) // 2

    mono_2022 = check_monotone("2022")
    mono_2023 = check_monotone("2023")
    monotone_both = mono_2022 and mono_2023

    if not monotone_both:
        rule = "STOP_ENTIRELY"
        explanation = (
            "Abstention does NOT monotonically improve ROI in both windows. "
            "The selection penalty is diffuse — not concentrated in low-confidence bets. "
            "Prediction-side improvements cannot close the gap."
        )
    elif (isinstance(w22, dict) and isinstance(w23, dict) and
          w22.get("pin_clv_ci_gt0") and w23.get("pin_clv_ci_gt0") and
          w22.get("meets_n") and w23.get("meets_n") and
          w22.get("meets_sp") and w23.get("meets_sp")):
        rule = "CONTINUE_PREDICTION"
        explanation = (
            "Both windows: abstention improves ROI, Pinnacle CLV CI>0, ≥500 bets, SP<4pp. "
            "Continue prediction-side work. Phase 9 target: strengthen xG or add exchange timing."
        )
    elif (isinstance(w22, dict) and isinstance(w23, dict) and
          (w22.get("pin_clv_ci_gt0") or w23.get("pin_clv_ci_gt0")) and
          not (w22.get("meets_n") and w23.get("meets_n"))):
        rule = "PIVOT_MARKET_STRUCTURE"
        explanation = (
            "CLV survives vs Pinnacle in ≥1 window but selective set falls below 500-bet floor. "
            "Prediction signal exists but selection rate too low for statistical power. "
            "Pivot: access liquid exchange (Betfair) or expand coverage to more leagues."
        )
    elif isinstance(w22, dict) and isinstance(w23, dict):
        rule = "PIVOT_MARKET_STRUCTURE"
        explanation = (
            "Improved ROI but Pinnacle CLV does not confirm edge in both windows. "
            "Edge may be B365-specific (closing-line inefficiency, not true forecast edge). "
            "Pivot to market-structure investigation."
        )
    else:
        rule = "STOP_ENTIRELY"
        explanation = (
            "Abstention does not improve ROI in either window. "
            "No evidence that selection penalty is reducible via confidence filtering."
        )

    verdict = {
        "rule": rule,
        "explanation": explanation,
        "window_2022": w22,
        "window_2023": w23,
        "mono_roi_2022": mono_2022,
        "mono_roi_2023": mono_2023,
    }
    print(f"\n  STOPPING RULE → {rule}")
    print(f"  {explanation}")
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 — Reconciliation check
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_with_phase7(bets_2022: list, bets_2023: list) -> bool:
    """Assert regenerated Var-B roll10 aggregates match phase7_results.json."""
    expected = {
        "2022": {"n": 764, "roi": -2.542, "clv": 2.073, "n_clv": 757},
        "2023": {"n": 1355, "roi": -20.163, "clv": 1.686, "n_clv": 1348},
    }
    ok = True
    for win_name, bets in [("2022", bets_2022), ("2023", bets_2023)]:
        exp = expected[win_name]
        n = len(bets)
        roi = np.mean([b["roi"] for b in bets]) * 100 if bets else float("nan")
        clv_vals = [b["clv"] for b in bets if b.get("clv") is not None]
        clv = np.mean(clv_vals) * 100 if clv_vals else float("nan")
        n_clv = len(clv_vals)

        n_ok  = abs(n - exp["n"]) <= 5        # allow ±5 for rounding
        roi_ok = abs(roi - exp["roi"]) < 0.5  # <0.5pp tolerance
        clv_ok = abs(clv - exp["clv"]) < 0.2

        status = "OK" if (n_ok and roi_ok and clv_ok) else "MISMATCH"
        if status == "MISMATCH":
            ok = False
        print(f"  Reconcile {win_name}: n={n} (exp {exp['n']}) "
              f"ROI={roi:.3f}% (exp {exp['roi']}) "
              f"CLV={clv:.3f}% (exp {exp['clv']}) → {status}")
        if not n_ok:
            print(f"    WARNING: bet count mismatch Δ={n - exp['n']}")
        if not roi_ok:
            print(f"    WARNING: ROI mismatch Δ={roi - exp['roi']:.3f}pp")
        if not clv_ok:
            print(f"    WARNING: CLV mismatch Δ={clv - exp['clv']:.3f}pp")

    if not ok:
        print("  RECONCILE FAILED — downstream results may be invalid. Proceeding with caution.")
    else:
        print("  RECONCILE PASSED — aggregate numbers match Phase 7 published results.")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 — Report generation
# ─────────────────────────────────────────────────────────────────────────────

def format_pct(v, dp=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{dp}f}%"


def write_report(full_results: dict, out_path: Path):
    lines = []
    lines.append("# Phase 8 — Selective Prediction / Calibrated Abstention\n")
    lines.append("> **Scope:** Var-B DC+xG roll=10 (Phase 7 primary model).")
    lines.append("> **Leagues:** EPL (39), Serie A (135), La Liga (140).")
    lines.append("> **Windows:** 2022 (Jan–Dec 2022), 2023 (Jan 2023–Jun 2024).")
    lines.append("> **Calibration:** 2022 window ← pre-2022 fdco training bets; "
                 "2023 window ← 2022 validation bets (forward-in-time only).")
    lines.append("> **Abstention signal:** p_selected (DC+xG model probability of bet direction).\n")

    # Reconciliation
    lines.append("## Reconciliation with Phase 7\n")
    rec = full_results.get("reconcile", {})
    lines.append("Regenerated per-bet records must match Phase 7 published numbers:\n")
    lines.append("| Window | Expected n | Got n | Expected ROI | Got ROI | Match? |")
    lines.append("|--------|------------|-------|-------------|---------|--------|")
    for win_name in ["2022", "2023"]:
        r = rec.get(win_name, {})
        lines.append(f"| {win_name} | {r.get('expected_n','?')} | {r.get('got_n','?')} | "
                     f"{r.get('expected_roi','?')}% | {r.get('got_roi','?')}% | "
                     f"{'✓' if r.get('ok') else '✗ FAIL'} |")
    lines.append("")

    # Task 1
    lines.append("## Task 1 — Reliability Signal Inventory\n")
    t1 = full_results.get("task1", {})
    for win_name in ["2022", "2023"]:
        t1w = t1.get(win_name, {})
        if not t1w:
            continue
        lines.append(f"### Window {win_name} ({t1w.get('n_bets','?')} bets)\n")

        # p_selected deciles
        sig_p = t1w.get("signal_p_selected", {})
        dec   = sig_p.get("deciles", [])
        mono  = sig_p.get("monotonicity", {})
        lines.append("**Signal 1: p_selected (model confidence) — ROI by decile**\n")
        lines.append(f"Monotonicity: {mono.get('direction','?')} "
                     f"({mono.get('n_increasing','?')}/{mono.get('n_deciles',0)-1} steps +)\n")
        lines.append("| Decile | p_selected range | n | ROI% | [95% CI] | CLV% | SP% |")
        lines.append("|--------|-----------------|---|------|----------|------|-----|")
        for d in dec:
            lines.append(
                f"| D{d['decile']:02d} | [{format_pct(d['signal_lo']*100,2)}, "
                f"{format_pct(d['signal_hi']*100,2)}] | {d['n']} | "
                f"{format_pct(d['roi'])} | [{format_pct(d['roi_lo'])},{format_pct(d['roi_hi'])}] | "
                f"{format_pct(d['clv'])} | {format_pct(d['sel_penalty'])} |"
            )
        lines.append("")

        # Disagreement deciles (summary only)
        sig_d = t1w.get("signal_disagreement", {})
        lines.append(f"**Signal 2: |p_model − p_market| disagreement**\n")
        mono_d = sig_d.get("monotonicity", {})
        lines.append(f"Monotonicity: {mono_d.get('direction','?')} "
                     f"({mono_d.get('n_increasing','?')}/{mono_d.get('n_deciles',0)-1} steps +)\n")

        # xG depth summary
        sig_xg = t1w.get("signal_xg_depth", {})
        mono_xg = sig_xg.get("monotonicity", {})
        lines.append(f"**Signal 3: xG data depth (min n_xg_home, n_xg_away)**\n")
        lines.append(f"Monotonicity: {mono_xg.get('direction','?')} "
                     f"({mono_xg.get('n_increasing','?')}/{mono_xg.get('n_deciles',0)-1} steps +)\n")

        # League breakdown
        sig_l = t1w.get("signal_league", {})
        lines.append("**Signal 4: Per-league ROI**\n")
        lines.append("| League | n | ROI% | CI lo | CI hi |")
        lines.append("|--------|---|------|-------|-------|")
        for lid_str, s in sig_l.items():
            lines.append(f"| {lid_str} | {s.get('n','?')} | "
                         f"{format_pct(s.get('roi'))} | {format_pct(s.get('ci_lo'))} | "
                         f"{format_pct(s.get('ci_hi'))} |")
        lines.append("")

        # Best signal summary
        lines.append(f"**Summary:** best monotone signal = `{t1w.get('best_monotone_signal','?')}` "
                     f"({t1w.get('best_monotone_n_increasing','?')} of "
                     f"{mono.get('n_deciles',0)-1} decile steps show +ROI direction)\n")

    # Task 2
    lines.append("## Task 2 — Selective Prediction Layer\n")
    lines.append("Calibration: p_selected threshold from prior-in-time training bets.\n")
    t2 = full_results.get("task2", {})
    for win_name in ["2022", "2023"]:
        t2w = t2.get(win_name, {})
        if not t2w:
            continue
        lines.append(f"### Window {win_name} "
                     f"(calibration n={t2w.get('calibration_n','?')})\n")
        lines.append("| Abstention | τ | n bets | ROI% | [95% CI] | CLV B365% | CLV Pin% | SP% |")
        lines.append("|-----------|---|--------|------|----------|-----------|----------|-----|")
        for row in t2w.get("rows", []):
            ar = row.get("abstention_rate_nominal", 0)
            tau = row.get("threshold")
            tau_str = f"{tau:.3f}" if tau is not None else "—"
            n   = row.get("n_bets", 0)
            roi = row.get("roi", {}) or {}
            clv_b = row.get("clv_b365", {}) or {}
            clv_p = row.get("clv_pinnacle", {}) or {}
            sp  = row.get("selection_penalty_pct")
            lines.append(
                f"| {ar:.0%} | {tau_str} | {n} | "
                f"{format_pct(roi.get('roi'))} | [{format_pct(roi.get('ci_lo'))},"
                f"{format_pct(roi.get('ci_hi'))}] | {format_pct(clv_b.get('clv'))} | "
                f"{format_pct(clv_p.get('clv'))} | {format_pct(sp)} |"
            )
        lines.append("")

    # Task 3
    lines.append("## Task 3 — Pinnacle CLV Cross-Check\n")
    lines.append("Realized ROI is book-independent (bet at B365 open). "
                 "Two CLV measures test whether the B365-priced edge survives "
                 "against the sharp-market final price.\n")
    lines.append("| Window | Abstention | n | CLV vs B365 close | CI | CLV vs Pinnacle close | CI |")
    lines.append("|--------|-----------|---|-------------------|-----|----------------------|----|")
    for win_name in ["2022", "2023"]:
        t2w = t2.get(win_name, {})
        for row in (t2w.get("rows") or []):
            ar  = row.get("abstention_rate_nominal", 0)
            clv_b = row.get("clv_b365", {}) or {}
            clv_p = row.get("clv_pinnacle", {}) or {}
            n = row.get("n_bets", 0)
            lines.append(
                f"| {win_name} | {ar:.0%} | {n} | "
                f"{format_pct(clv_b.get('clv'))} | [{format_pct(clv_b.get('ci_lo'))},"
                f"{format_pct(clv_b.get('ci_hi'))}] | "
                f"{format_pct(clv_p.get('clv'))} | [{format_pct(clv_p.get('ci_lo'))},"
                f"{format_pct(clv_p.get('ci_hi'))}] |"
            )
    lines.append("")

    # Task 4
    lines.append("## Task 4 — Stopping Rule\n")
    t4 = full_results.get("task4", {})
    rule = t4.get("rule", "?")
    lines.append(f"**Decision: {rule}**\n")
    lines.append(f"{t4.get('explanation','')}\n")
    lines.append("Per-window criteria:\n")
    lines.append("| Criterion | 2022 | 2023 |")
    lines.append("|-----------|------|------|")
    w22 = t4.get("window_2022") or {}
    w23 = t4.get("window_2023") or {}
    lines.append(f"| Abstention improves ROI | "
                 f"{'YES' if isinstance(w22, dict) and w22.get('roi') else 'NO'} | "
                 f"{'YES' if isinstance(w23, dict) and w23.get('roi') else 'NO'} |")
    lines.append(f"| Pinnacle CLV CI > 0 | "
                 f"{'YES' if isinstance(w22, dict) and w22.get('pin_clv_ci_gt0') else 'NO'} | "
                 f"{'YES' if isinstance(w23, dict) and w23.get('pin_clv_ci_gt0') else 'NO'} |")
    lines.append(f"| ≥500 bets in selective set | "
                 f"{'YES' if isinstance(w22, dict) and w22.get('meets_n') else 'NO'} | "
                 f"{'YES' if isinstance(w23, dict) and w23.get('meets_n') else 'NO'} |")
    lines.append(f"| Selection penalty < 4pp | "
                 f"{'YES' if isinstance(w22, dict) and w22.get('meets_sp') else 'NO'} | "
                 f"{'YES' if isinstance(w23, dict) and w23.get('meets_sp') else 'NO'} |")
    lines.append(f"| ROI monotone in abstention | "
                 f"{'YES' if t4.get('mono_roi_2022') else 'NO'} | "
                 f"{'YES' if t4.get('mono_roi_2023') else 'NO'} |")
    lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"\nReport: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print("=== Phase 8 — Selective Prediction / Calibrated Abstention ===\n")

    fdco_cache = ANALYSIS / "fdco_cache"

    # ── Load Understat matches ─────────────────────────────────────────────
    print("Loading Understat matches from cache...")
    all_matches = p7.fetch_understat_all()

    # ── Load production DB for team_id_map ────────────────────────────────
    engine = create_engine("sqlite:////opt/projects/bootball/data/football.db")
    team_id_map = p7.build_team_id_to_understat(engine)
    print(f"Team ID map: {len(team_id_map)} teams")

    # ── Build team_history ────────────────────────────────────────────────
    team_history = defaultdict(list)
    for m in all_matches:
        hn = p7.normalize(m["home_title"])
        an = p7.normalize(m["away_title"])
        d  = m["date"]
        team_history[hn].append((d, m["xg_home"], m["xg_away"]))
        team_history[an].append((d, m["xg_away"], m["xg_home"]))
    for name in team_history:
        team_history[name].sort(key=lambda x: x[0])
    print(f"Team history: {len(team_history)} teams")

    # ── Load pred cache ───────────────────────────────────────────────────
    preds_all = []
    for win_name in ["2022", "2023"]:
        raw = json.loads((ANALYSIS / "dc_cache" / f"preds_{win_name}.json").read_text())
        preds_all.extend(raw)
    preds_covered = [p for p in preds_all if p["league_id"] in FDCO_LEAGUES]
    preds_by_id   = {p["id"]: p for p in preds_all}
    print(f"Pred cache: {len(preds_all)} total, {len(preds_covered)} covered leagues")

    # ── Load DC cache (rho values) ─────────────────────────────────────────
    dc_cache_all = {}
    for win_name in ["2022", "2023"]:
        dc_cache_all[win_name] = json.loads(
            (ANALYSIS / "dc_cache" / f"dc_{win_name}.json").read_text())

    # ── Load B365 closing odds ─────────────────────────────────────────────
    closing_map = p7.load_closing_map()
    print(f"Closing odds: {len(closing_map)} fixtures")

    # ── Regenerate per-bet records (Var-B roll=10) ─────────────────────────
    print("\nRegenerating Var-B roll=10 walk-forward records...")
    wf = p7.run_walk_forward(
        preds_covered, all_matches, team_id_map, team_history,
        dc_cache_all, closing_map, ROLL_WINDOW)

    raw_2022 = wf["2022"]["variant_b"]
    raw_2023 = wf["2023"]["variant_b"]
    sp_2022  = {int(k): tuple(v) for k, v in wf["2022"]["skellam_params"].items()}
    sp_2023  = {int(k): tuple(v) for k, v in wf["2023"]["skellam_params"].items()}

    # ── Reconcile with Phase 7 ────────────────────────────────────────────
    print("\nReconciliation check:")
    reconcile_ok = reconcile_with_phase7(raw_2022, raw_2023)
    rec_detail = {}
    for win_name, bets in [("2022", raw_2022), ("2023", raw_2023)]:
        exp_n   = 764 if win_name == "2022" else 1355
        exp_roi = -2.542 if win_name == "2022" else -20.163
        got_roi = np.mean([b["roi"] for b in bets]) * 100 if bets else float("nan")
        rec_detail[win_name] = {
            "expected_n": exp_n,
            "got_n": len(bets),
            "expected_roi": exp_roi,
            "got_roi": round(float(got_roi), 3),
            "ok": abs(len(bets) - exp_n) <= 5 and abs(float(got_roi) - exp_roi) < 0.5,
        }

    # ── Augment bets with p_selected, disagreement, market odds ──────────
    print("\nAugmenting validation bets with p_selected, market probs...")
    bets_2022 = augment_bets(raw_2022, preds_by_id)
    bets_2023 = augment_bets(raw_2023, preds_by_id)

    # ── Build Pinnacle closing map ─────────────────────────────────────────
    print("\nBuilding Pinnacle closing map from fdco CSVs...")
    pin_map = build_pinnacle_closing_map(fdco_cache)

    # ── Enrich with Pinnacle CLV ───────────────────────────────────────────
    print("\nJoining Pinnacle closing odds to validation bets...")
    print("  2022 window:")
    bets_2022 = enrich_with_pinnacle(bets_2022, pin_map, preds_by_id)
    print("  2023 window:")
    bets_2023 = enrich_with_pinnacle(bets_2023, pin_map, preds_by_id)

    # ── Build pre-2022 training bets ───────────────────────────────────────
    print("\nBuilding pre-2022 training bets from fdco CSVs...")
    dc_rho_2022 = {
        lid: p7._get_rho(dc_cache_all, "2022", lid)
        for lid in FDCO_LEAGUES
    }
    train_bets_2022 = build_pre_2022_training_bets(
        fdco_cache, team_id_map, team_history,
        skellam_params=sp_2022,
        dc_rho=dc_rho_2022,
        blend_w=WINDOWS["2022"]["blend_w"],
    )
    if not train_bets_2022:
        print("  WARN: No pre-2022 training bets produced — check fdco CSV coverage")

    # ── Task 1: Reliability signal inventory ─────────────────────────────
    print("\n--- Task 1: Reliability Signal Inventory ---")
    t1_2022 = task1_signals(bets_2022, "2022")
    t1_2023 = task1_signals(bets_2023, "2023")

    # ── Task 2: Conformal abstention ───────────────────────────────────────
    print("\n--- Task 2: Selective Prediction Layer ---")
    t2_results = task2_abstention(
        bets_2022, bets_2023, train_bets_2022,
        task1_results={"2022": t1_2022, "2023": t1_2023})

    # ── Task 4: Stopping rule ──────────────────────────────────────────────
    print("\n--- Task 4: Stopping Rule ---")
    t4_verdict = task4_stopping_rule(t2_results)

    # ── Assemble full results ──────────────────────────────────────────────
    full_results = {
        "meta": {
            "phase": 8,
            "model": "Var-B DC+xG roll=10",
            "run_date": datetime.now().strftime("%Y-%m-%d"),
            "reconcile_ok": reconcile_ok,
        },
        "reconcile": rec_detail,
        "task1": {"2022": t1_2022, "2023": t1_2023},
        "task2": t2_results,
        "task4": t4_verdict,
        "training_bets_2022": {
            "n": len(train_bets_2022),
            "roi": round(np.mean([b["roi"] for b in train_bets_2022]) * 100, 3) if train_bets_2022 else None,
        },
    }

    # ── Save JSON ──────────────────────────────────────────────────────────
    out_json = ANALYSIS / "phase8_results.json"
    out_json.write_text(json.dumps(full_results, indent=2, default=str))
    print(f"\nResults saved: {out_json}")

    # ── Write report ───────────────────────────────────────────────────────
    write_report(full_results, ANALYSIS / "v8_selective_report.md")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
