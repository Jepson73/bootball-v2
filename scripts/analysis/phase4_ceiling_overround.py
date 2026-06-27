"""
Phase 4 Task 2 + Task 3: Odds-Ceiling Re-Evaluation and Margin Segmentation.

Task 2: Re-slice DC predictions with odds ceiling ≤2.0, ≤2.5, ≤3.0.
Task 3: Compute per-fixture bookmaker overround from stored odds; bucket
        by overround quartile; report DC model ROI and calibration per bucket;
        model-free control in any clearing bucket; held-out replication check.

Data source: scripts/analysis/dc_cache/preds_{window}.json (all three windows).
Phase 3 blend weights: 2022=1.0, 2023=0.65, 2025-26=0.35.
"""
from __future__ import annotations

import json
import math
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "dc_cache"
REPORT    = Path(__file__).parent / "v4_ceiling_overround_report.md"
RESULTS   = Path(__file__).parent / "phase4_ceiling_overround.json"

DC_BLEND_W   = {"2022": 1.0, "2023": 0.65, "2025-26": 0.35}
BOT_MIN_EV   = 0.05
N_BOOTSTRAP  = 5000
CEILINGS     = [2.0, 2.5, 3.0]
WINDOWS      = ["2022", "2023", "2025-26"]
FDCO_WINDOWS = {"2022", "2023"}


# ── utils ─────────────────────────────────────────────────────────────────────

def shin_probabilities(odds: List[float]) -> List[float]:
    n = len(odds)
    raw = [1.0 / o for o in odds]
    over = sum(raw)
    if n == 2:
        z_disc = 1.0 - 4.0*(over-1.0)*sum(r**2 for r in raw)/over**2
        z = (1.0 - math.sqrt(max(z_disc, 0.0))) / (2.0*(over-1.0)) if over > 1 else 0.0
        probs = [(math.sqrt(z**2 + 4*(1-z)*r/over) - z)/(2*(1-z))
                 if (1-z) > 1e-9 else r/over for r in raw]
    else:
        probs = [r/over for r in raw]
    s = sum(probs); return [p/s for p in probs]


def bootstrap_roi_ci(pnls: List[float], n: int = N_BOOTSTRAP) -> Tuple[float, float]:
    if not pnls:
        return (0.0, 0.0)
    rng = np.random.default_rng(42)
    a   = np.array(pnls)
    samples = rng.choice(a, size=(n, len(a)), replace=True).mean(axis=1)
    return (float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5)))


def overround(odds: List[float]) -> Optional[float]:
    """Sum of implied probabilities across all outcomes in a market."""
    if not all(o and o > 1.0 for o in odds):
        return None
    return sum(1.0/o for o in odds)


def h2h_outcome(gh, ga) -> Optional[int]:
    if gh is None or ga is None: return None
    return 0 if gh > ga else (1 if gh == ga else 2)

def ou25_outcome(gh, ga) -> Optional[int]:
    if gh is None or ga is None: return None
    return 1 if (gh + ga) > 2 else 0

def btts_outcome(gh, ga) -> Optional[int]:
    if gh is None or ga is None: return None
    return 1 if (gh > 0 and ga > 0) else 0

def ou15_outcome(gh, ga) -> Optional[int]:
    if gh is None or ga is None: return None
    return 1 if (gh + ga) > 1 else 0


# ── Enumerate bets from a prediction record ───────────────────────────────────

def enumerate_bets(pred: dict, blend_w: float, odds_ceiling: Optional[float] = None,
                   market_filter: Optional[str] = None) -> List[dict]:
    """
    Return all bets that pass the EV filter (and optional odds ceiling).
    Each bet dict: market, odds, won, pnl, p_blend, overround, ev, date, league_id.
    """
    gh, ga = pred.get("goals_home"), pred.get("goals_away")
    dc     = pred.get("dc", {})
    bets   = []

    def add(market, odds_list, outcome_idx, won, p_blend_val, or_val):
        if won is None: return
        o = odds_list[outcome_idx]
        if not (o and o > 1.01): return
        if odds_ceiling and o > odds_ceiling: return
        ev = p_blend_val * o - 1.0
        if ev <= BOT_MIN_EV: return
        pnl = (o - 1.0) if won else -1.0
        if market_filter and market != market_filter: return
        bets.append({
            "market": market, "odds": o, "won": bool(won), "pnl": pnl,
            "p_blend": p_blend_val, "ev": ev, "overround": or_val,
            "date": pred.get("date"), "league_id": pred.get("league_id"),
        })

    # h2h
    h2h_odds = [pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away")]
    ph = dc.get("p_h2h")
    if ph and all(o and o > 1.01 for o in h2h_odds):
        shin = shin_probabilities(h2h_odds)
        ornd = overround(h2h_odds)
        lbl  = h2h_outcome(gh, ga)
        for idx, (p_dc, p_sh) in enumerate(zip(ph, shin)):
            pb = blend_w*p_dc + (1-blend_w)*p_sh
            add("h2h", h2h_odds, idx, lbl == idx if lbl is not None else None, pb, ornd)

    # ou25
    o_ov, o_un = pred.get("odd_ou25_over"), pred.get("odd_ou25_under")
    pov = dc.get("p_ou25_over")
    if o_ov and o_un and o_ov > 1.01 and o_un > 1.01 and pov is not None:
        shin = shin_probabilities([o_ov, o_un])
        ornd = overround([o_ov, o_un])
        lbl  = ou25_outcome(gh, ga)
        for is_ov, o, p_dc, p_sh in [(True, o_ov, pov, shin[0]), (False, o_un, 1-pov, shin[1])]:
            pb = blend_w*p_dc + (1-blend_w)*p_sh
            tmp_odds = [o_ov, o_un]; tmp_idx = 0 if is_ov else 1
            add("ou25", tmp_odds, tmp_idx, (lbl == (1 if is_ov else 0)) if lbl is not None else None, pb, ornd)

    # btts
    o_by, o_bn = pred.get("odd_btts_yes"), pred.get("odd_btts_no")
    pby = dc.get("p_btts_yes")
    if o_by and o_bn and o_by > 1.01 and o_bn > 1.01 and pby is not None:
        shin = shin_probabilities([o_by, o_bn])
        ornd = overround([o_by, o_bn])
        lbl  = btts_outcome(gh, ga)
        for is_y, o, p_dc, p_sh in [(True, o_by, pby, shin[0]), (False, o_bn, 1-pby, shin[1])]:
            pb = blend_w*p_dc + (1-blend_w)*p_sh
            tmp = [o_by, o_bn]; tmp_idx = 0 if is_y else 1
            add("btts", tmp, tmp_idx, (lbl == (1 if is_y else 0)) if lbl is not None else None, pb, ornd)

    # ou15
    o_15o, o_15u = pred.get("odd_ou15_over"), pred.get("odd_ou15_under")
    p15 = dc.get("p_ou15_over")
    if o_15o and o_15u and o_15o > 1.01 and o_15u > 1.01 and p15 is not None:
        shin = shin_probabilities([o_15o, o_15u])
        ornd = overround([o_15o, o_15u])
        lbl  = ou15_outcome(gh, ga)
        for is_ov, o, p_dc, p_sh in [(True, o_15o, p15, shin[0]), (False, o_15u, 1-p15, shin[1])]:
            pb = blend_w*p_dc + (1-blend_w)*p_sh
            tmp = [o_15o, o_15u]; tmp_idx = 0 if is_ov else 1
            add("ou15", tmp, tmp_idx, (lbl == (1 if is_ov else 0)) if lbl is not None else None, pb, ornd)

    return bets


def enumerate_naive_bets(pred: dict, odds_ceiling: Optional[float] = None,
                          market_filter: Optional[str] = None,
                          overround_filter: Optional[Tuple[float,float]] = None) -> List[dict]:
    """
    All available bets at given odds, no model, no EV filter — for control check.
    Applies optional odds_ceiling and overround_filter.
    """
    gh, ga = pred.get("goals_home"), pred.get("goals_away")
    bets = []

    def add(market, odds_val, won, or_val):
        if won is None or not (odds_val and odds_val > 1.01): return
        if odds_ceiling and odds_val > odds_ceiling: return
        if overround_filter:
            lo, hi = overround_filter
            if or_val is None or not (lo <= or_val < hi): return
        if market_filter and market != market_filter: return
        pnl = (odds_val - 1.0) if won else -1.0
        bets.append({"market": market, "odds": odds_val, "won": bool(won),
                      "pnl": pnl, "overround": or_val,
                      "date": pred.get("date"), "league_id": pred.get("league_id")})

    h2h_odds = [pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away")]
    if all(o and o > 1.01 for o in h2h_odds):
        ornd = overround(h2h_odds)
        lbl  = h2h_outcome(gh, ga)
        for idx, o in enumerate(h2h_odds):
            add("h2h", o, lbl == idx if lbl is not None else None, ornd)

    o_ov, o_un = pred.get("odd_ou25_over"), pred.get("odd_ou25_under")
    if o_ov and o_un and o_ov > 1.01 and o_un > 1.01:
        ornd = overround([o_ov, o_un]); lbl = ou25_outcome(gh, ga)
        add("ou25", o_ov, lbl == 1 if lbl is not None else None, ornd)
        add("ou25", o_un, lbl == 0 if lbl is not None else None, ornd)

    o_by, o_bn = pred.get("odd_btts_yes"), pred.get("odd_btts_no")
    if o_by and o_bn and o_by > 1.01 and o_bn > 1.01:
        ornd = overround([o_by, o_bn]); lbl = btts_outcome(gh, ga)
        add("btts", o_by, lbl == 1 if lbl is not None else None, ornd)
        add("btts", o_bn, lbl == 0 if lbl is not None else None, ornd)

    o_15o, o_15u = pred.get("odd_ou15_over"), pred.get("odd_ou15_under")
    if o_15o and o_15u and o_15o > 1.01 and o_15u > 1.01:
        ornd = overround([o_15o, o_15u]); lbl = ou15_outcome(gh, ga)
        add("ou15", o_15o, lbl == 1 if lbl is not None else None, ornd)
        add("ou15", o_15u, lbl == 0 if lbl is not None else None, ornd)

    return bets


def roi_summary(pnls: List[float], label: str = "") -> dict:
    if not pnls:
        return {"n": 0, "roi": None, "ci_lo": None, "ci_hi": None, "pass_500": False, "ci_excl_zero": False}
    roi = float(np.mean(pnls))
    ci  = bootstrap_roi_ci(pnls)
    return {
        "n": len(pnls), "roi": round(roi, 4),
        "ci_lo": round(ci[0], 4), "ci_hi": round(ci[1], 4),
        "pass_500": len(pnls) >= 500, "ci_excl_zero": ci[0] > 0,
    }


# ── Load all preds ────────────────────────────────────────────────────────────

def load_all_preds() -> Dict[str, List[dict]]:
    preds = {}
    for wname in WINDOWS:
        path = CACHE_DIR / f"preds_{wname}.json"
        if path.exists():
            preds[wname] = json.loads(path.read_text())
            logger.info(f"Loaded {len(preds[wname])} preds for {wname}")
        else:
            logger.warning(f"No cache for {wname}")
    return preds


# ── Task 2: Odds Ceiling ──────────────────────────────────────────────────────

def task2_ceiling(all_preds: Dict[str, List[dict]]) -> dict:
    """
    For each ceiling × market × window: collect DC EV-filtered bets.
    Also report uncapped baseline (ceiling=None) for comparison.
    """
    results = {}
    ceilings_to_test = [None] + CEILINGS

    for market in ["h2h", "ou25", "btts", "ou15"]:
        results[market] = {}
        for ceiling in ceilings_to_test:
            key = f"ceil_{ceiling}" if ceiling else "no_ceil"
            per_window = {}
            for wname in WINDOWS:
                preds = all_preds.get(wname, [])
                w = DC_BLEND_W[wname]
                pnls = []
                for pred in preds:
                    for b in enumerate_bets(pred, w, odds_ceiling=ceiling, market_filter=market):
                        pnls.append(b["pnl"])
                per_window[wname] = roi_summary(pnls)

            n_pass = sum(1 for v in per_window.values() if v["pass_500"] and v["ci_excl_zero"])
            results[market][key] = {
                "ceiling": ceiling,
                "per_window": per_window,
                "n_windows_pass": n_pass,
                "bar_met": n_pass >= 2,
            }
            logger.info(f"  {market} ceiling={ceiling}: "
                        f"{[v['n'] for v in per_window.values()]} bets/window, "
                        f"ROI={[v['roi'] for v in per_window.values()]}")

    return results


# ── Task 3: Overround Segmentation ───────────────────────────────────────────

def task3_overround(all_preds: Dict[str, List[dict]]) -> dict:
    """
    Bucket all validation fixtures by per-market overround.
    Use h2h and ou25 (most data).
    Report DC ROI + naive ROI per bucket.
    Held-out replication: discover buckets on fdco windows (2022/2023);
    test replication on production window (2025-26).
    """
    # Step 1: compute all overrounds across the full pool to get quartile cutoffs
    all_or_h2h  = []
    all_or_ou25 = []
    all_or_all  = []  # combined for a single set of global cuts

    for wname in WINDOWS:
        for pred in all_preds.get(wname, []):
            h2h_odds = [pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away")]
            if all(o and o > 1.01 for o in h2h_odds):
                all_or_h2h.append(overround(h2h_odds))
                all_or_all.append(overround(h2h_odds))
            o_ov, o_un = pred.get("odd_ou25_over"), pred.get("odd_ou25_under")
            if o_ov and o_un and o_ov > 1.01 and o_un > 1.01:
                all_or_ou25.append(overround([o_ov, o_un]))
                all_or_all.append(overround([o_ov, o_un]))

    # Quartile cuts
    def quartile_cuts(values: List[float]) -> Tuple[float, float, float]:
        s = sorted(values)
        n = len(s)
        return s[n//4], s[n//2], s[3*n//4]

    q1_h2h, q2_h2h, q3_h2h = quartile_cuts(all_or_h2h)
    q1_ou25, q2_ou25, q3_ou25 = quartile_cuts(all_or_ou25)

    logger.info(f"h2h overround quartiles: {q1_h2h:.4f} / {q2_h2h:.4f} / {q3_h2h:.4f}")
    logger.info(f"ou25 overround quartiles: {q1_ou25:.4f} / {q2_ou25:.4f} / {q3_ou25:.4f}")

    def bucket_label(ornd: float, q1: float, q2: float, q3: float) -> str:
        if ornd < q1:    return "Q1 (tightest)"
        if ornd < q2:    return "Q2"
        if ornd < q3:    return "Q3"
        return "Q4 (loosest)"

    results = {}

    for market, q1, q2, q3 in [("h2h", q1_h2h, q2_h2h, q3_h2h),
                                  ("ou25", q1_ou25, q2_ou25, q3_ou25)]:
        buckets = ["Q1 (tightest)", "Q2", "Q3", "Q4 (loosest)"]
        bucket_cuts = {"Q1 (tightest)": (0, q1), "Q2": (q1, q2), "Q3": (q2, q3), "Q4 (loosest)": (q3, 9.9)}

        # A: full pool (all 3 windows) — discovery phase
        dc_by_bucket:    Dict[str, List[float]] = defaultdict(list)
        naive_by_bucket: Dict[str, List[float]] = defaultdict(list)
        # B: fdco windows only (2022, 2023) — discovery subset
        dc_fdco:    Dict[str, List[float]] = defaultdict(list)
        naive_fdco: Dict[str, List[float]] = defaultdict(list)
        # C: production window (2025-26) — held-out replication
        dc_prod:    Dict[str, List[float]] = defaultdict(list)
        naive_prod: Dict[str, List[float]] = defaultdict(list)

        for wname in WINDOWS:
            w = DC_BLEND_W[wname]
            is_fdco = wname in FDCO_WINDOWS
            for pred in all_preds.get(wname, []):
                # DC EV-filtered bets
                for b in enumerate_bets(pred, w, market_filter=market):
                    ornd = b.get("overround")
                    if ornd is None: continue
                    lbl = bucket_label(ornd, q1, q2, q3)
                    dc_by_bucket[lbl].append(b["pnl"])
                    if is_fdco:   dc_fdco[lbl].append(b["pnl"])
                    else:         dc_prod[lbl].append(b["pnl"])

                # Naive bets (all outcomes, no EV filter, same market)
                for b in enumerate_naive_bets(pred, market_filter=market):
                    ornd = b.get("overround")
                    if ornd is None: continue
                    lbl = bucket_label(ornd, q1, q2, q3)
                    naive_by_bucket[lbl].append(b["pnl"])
                    if is_fdco:   naive_fdco[lbl].append(b["pnl"])
                    else:         naive_prod[lbl].append(b["pnl"])

        bucket_results = {}
        for bkt in buckets:
            lo, hi = bucket_cuts[bkt]
            r = {
                "overround_range": (round(lo, 4), round(hi, 4)),
                "dc_full":     roi_summary(dc_by_bucket[bkt]),
                "naive_full":  roi_summary(naive_by_bucket[bkt]),
                "dc_fdco":     roi_summary(dc_fdco[bkt]),
                "naive_fdco":  roi_summary(naive_fdco[bkt]),
                "dc_prod_holdout":    roi_summary(dc_prod[bkt]),
                "naive_prod_holdout": roi_summary(naive_prod[bkt]),
            }
            bucket_results[bkt] = r
            logger.info(f"  {market} {bkt}: DC n={r['dc_full']['n']}, roi={r['dc_full']['roi']}, "
                        f"naive n={r['naive_full']['n']}, roi={r['naive_full']['roi']}")

        results[market] = {
            "quartile_cuts": {"q1": round(q1,4), "q2": round(q2,4), "q3": round(q3,4)},
            "buckets": bucket_results,
        }

    return results


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(t2: dict, t3: dict) -> None:
    lines = []
    lines.append("# Phase 4 — Odds Ceiling + Overround Segmentation\n\n")

    # Task 2
    lines.append("## Task 2: Odds Ceiling Re-Evaluation (DC Model)\n\n")
    lines.append("*(Wave 1 LightGBM individual predictions are not cached; results below are DC only. "
                 "DC has higher AUC than Wave 1 in every window, so it is the more favourable test "
                 "of whether an odds ceiling rescues model performance.)*\n\n")
    lines.append("*Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows passing.*\n\n")

    for market in ["h2h", "ou25"]:
        lines.append(f"### {market.upper()}\n\n")
        lines.append("| Ceiling | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |\n")
        lines.append("|---------|--------|--------|-----|--------|-------|-------|-------|\n")
        for ceil_key, ceil_label in [("no_ceil","No ceiling"),("ceil_2.0","≤2.0"),
                                      ("ceil_2.5","≤2.5"),("ceil_3.0","≤3.0")]:
            r = t2[market].get(ceil_key, {})
            for wname in WINDOWS:
                wv = r.get("per_window",{}).get(wname, {})
                n = wv.get("n", 0)
                if n == 0:
                    lines.append(f"| {ceil_label} | {wname} | 0 | — | — | NO | NO | FAIL |\n")
                else:
                    p5 = "YES" if wv.get("pass_500") else "NO"
                    ci0 = "YES" if wv.get("ci_excl_zero") else "NO"
                    pf  = "PASS" if (wv.get("pass_500") and wv.get("ci_excl_zero")) else "FAIL"
                    lines.append(f"| {ceil_label} | {wname} | {n:,} | {wv['roi']:+.1%} | "
                                  f"[{wv['ci_lo']:+.1%},{wv['ci_hi']:+.1%}] | {p5} | {ci0} | {pf} |\n")
            bar = r.get("bar_met", False)
            lines.append(f"| {ceil_label} | — | — | — | — | — | — | {'**BAR MET**' if bar else 'BAR NOT MET'} |\n")
            lines.append("| | | | | | | | |\n")
        lines.append("\n")

    # Task 3
    lines.append("## Task 3: Bookmaker Margin Segmentation\n\n")
    lines.append("Overround = sum of implied probabilities across all outcomes in a market. "
                 "Higher overround = wider bookmaker margin. Quartile cuts computed across the full "
                 "validation pool.\n\n")
    lines.append("**Discovery set:** fdco windows (2022 + 2023). "
                 "**Held-out replication:** production window (2025-26).\n\n")
    lines.append("*Multiple-comparisons note: bucket cutoffs are computed on the full pool, "
                 "results reported on the fdco windows as discovery. Any bucket passing the bar is "
                 "tested on the production held-out window. Given the many slices across all phases, "
                 "replication in the held-out window is required before treating any result as real.*\n\n")

    for market in ["h2h", "ou25"]:
        lines.append(f"### {market.upper()}\n\n")
        r = t3.get(market, {})
        cuts = r.get("quartile_cuts", {})
        lines.append(f"Overround quartile cuts: Q1/Q2 split={cuts.get('q1'):.4f}, "
                      f"Q2/Q3={cuts.get('q2'):.4f}, Q3/Q4={cuts.get('q3'):.4f}\n\n")
        lines.append("| Bucket | OR range | DC n (full) | DC ROI | DC CI | Naive n | Naive ROI | Naive CI |\n")
        lines.append("|--------|----------|-------------|--------|-------|---------|-----------|----------|\n")
        for bkt in ["Q1 (tightest)", "Q2", "Q3", "Q4 (loosest)"]:
            bv = r.get("buckets", {}).get(bkt, {})
            dc = bv.get("dc_full", {})
            na = bv.get("naive_full", {})
            dc_ci = f"[{dc.get('ci_lo',0):+.1%},{dc.get('ci_hi',0):+.1%}]" if dc.get("n",0) else "—"
            na_ci = f"[{na.get('ci_lo',0):+.1%},{na.get('ci_hi',0):+.1%}]" if na.get("n",0) else "—"
            lo, hi = bv.get("overround_range", (0,0))
            lines.append(f"| {bkt} | [{lo:.3f},{hi:.3f}) "
                          f"| {dc.get('n',0):,} | {dc.get('roi',0):+.1%} | {dc_ci} "
                          f"| {na.get('n',0):,} | {na.get('roi',0):+.1%} | {na_ci} |\n")
        lines.append("\n")

        lines.append(f"**Discovery (fdco 2022+2023 only) vs Held-out (2025-26):**\n\n")
        lines.append("| Bucket | DC fdco ROI | DC fdco CI | Naive fdco ROI | DC prod ROI | DC prod CI | Naive prod ROI |\n")
        lines.append("|--------|-------------|-----------|----------------|-------------|-----------|----------------|\n")
        for bkt in ["Q1 (tightest)", "Q2", "Q3", "Q4 (loosest)"]:
            bv   = r.get("buckets", {}).get(bkt, {})
            dcf  = bv.get("dc_fdco", {})
            naf  = bv.get("naive_fdco", {})
            dcp  = bv.get("dc_prod_holdout", {})
            nap  = bv.get("naive_prod_holdout", {})
            dcf_ci = f"[{dcf.get('ci_lo',0):+.1%},{dcf.get('ci_hi',0):+.1%}]" if dcf.get("n",0) else "—"
            dcp_ci = f"[{dcp.get('ci_lo',0):+.1%},{dcp.get('ci_hi',0):+.1%}]" if dcp.get("n",0) else "—"
            dcf_roi = f"{dcf.get('roi',0):+.1%}" if dcf.get("n",0) else "—"
            naf_roi = f"{naf.get('roi',0):+.1%}" if naf.get("n",0) else "—"
            dcp_roi = f"{dcp.get('roi',0):+.1%}" if dcp.get("n",0) else "—"
            nap_roi = f"{nap.get('roi',0):+.1%}" if nap.get("n",0) else "—"
            bar_fdco  = "✓PASS" if (dcf.get("pass_500") and dcf.get("ci_excl_zero")) else "FAIL"
            lines.append(f"| {bkt} | {dcf_roi} [{bar_fdco}] | {dcf_ci} | {naf_roi} "
                          f"| {dcp_roi} | {dcp_ci} | {nap_roi} |\n")
        lines.append("\n")

    REPORT.write_text("".join(lines))
    logger.info(f"Report written to {REPORT}")


def main():
    all_preds = load_all_preds()

    logger.info("Task 2: Odds ceiling analysis...")
    t2 = task2_ceiling(all_preds)

    logger.info("Task 3: Overround segmentation...")
    t3 = task3_overround(all_preds)

    RESULTS.write_text(json.dumps({"task2": t2, "task3": t3}, indent=2))
    write_report(t2, t3)
    logger.info("Done.")


if __name__ == "__main__":
    main()
