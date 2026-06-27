"""
Phase 4b Z1-Z4: Bookmaker Bias Hunt
Ground rules: read-only re-analysis of cached backtest data.

Z1: Favorite-longshot bias test
Z2: League liquidity segmentation
Z3: Naive contrarian walk-forward backtest
Z4: Line-timing data limitation note
"""
from __future__ import annotations

import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR  = Path(__file__).parent / "dc_cache"
REPORT     = Path(__file__).parent / "v4b_bias_hunt_report.md"
RESULTS    = Path(__file__).parent / "phase4b_results.json"

# Blend weights selected per window in Phase 3 DC run
DC_BLEND_W = {"2022": 1.0, "2023": 0.65, "2025-26": 0.35}

# fdco leagues — 8 well-established European leagues with consistent multi-season coverage
FDCO_LEAGUES = {39, 40, 41, 42, 135, 136, 140, 141}
# Top-5 flavour within fdco (for finer liquidity tier)
TOP_LEAGUES  = {39, 135, 140}   # EPL, Serie A, La Liga

BOT_MIN_EV  = 0.05
N_BOOTSTRAP = 5000

WINDOWS = [
    {"name": "2022",    "test_start": "2022-01-01", "test_end": "2023-01-01"},
    {"name": "2023",    "test_start": "2023-01-01", "test_end": "2025-01-01"},
    {"name": "2025-26", "test_start": "2025-01-01", "test_end": "2027-01-01"},
]

# Odds buckets for FLB analysis
BUCKETS = [
    ("< 1.5",  None,  1.5),
    ("1.5–2.5", 1.5,  2.5),
    ("2.5–4.0", 2.5,  4.0),
    ("4.0–7.0", 4.0,  7.0),
    ("7.0+",    7.0, None),
]


# ── utilities ─────────────────────────────────────────────────────────────────

def shin_probabilities(odds: List[float]) -> List[float]:
    """Shin de-vigging — same as Phase 3 implementation."""
    n = len(odds)
    raw = [1.0 / o for o in odds]
    overround = sum(raw)
    if n == 2:
        z_disc = 1.0 - 4.0 * (overround - 1.0) * sum(r ** 2 for r in raw) / overround ** 2
        z = (1.0 - math.sqrt(max(z_disc, 0.0))) / (2.0 * (overround - 1.0)) if overround > 1 else 0.0
        probs = [(math.sqrt(z ** 2 + 4 * (1 - z) * r / overround) - z) / (2 * (1 - z))
                 if (1 - z) > 1e-9 else r / overround
                 for r in raw]
    else:
        probs = [r / overround for r in raw]
    s = sum(probs)
    return [p / s for p in probs]


def bootstrap_roi_ci(pnls: List[float], n: int = N_BOOTSTRAP) -> Tuple[float, float]:
    if not pnls:
        return (0.0, 0.0)
    rng = np.random.default_rng(42)
    a = np.array(pnls)
    samples = rng.choice(a, size=(n, len(a)), replace=True).mean(axis=1)
    return (float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5)))


def odds_bucket(odds: float) -> str:
    for label, lo, hi in BUCKETS:
        if (lo is None or odds >= lo) and (hi is None or odds < hi):
            return label
    return "7.0+"


def league_tier(league_id: int) -> str:
    if league_id in TOP_LEAGUES:
        return "tier1_top"
    if league_id in FDCO_LEAGUES:
        return "tier2_mid"
    return "tier3_longtail"


def h2h_win(goals_home, goals_away, outcome_idx: int) -> Optional[bool]:
    if goals_home is None or goals_away is None:
        return None
    gh, ga = int(goals_home), int(goals_away)
    actual = 0 if gh > ga else (1 if gh == ga else 2)
    return actual == outcome_idx


def ou25_win(goals_home, goals_away, is_over: bool) -> Optional[bool]:
    if goals_home is None or goals_away is None:
        return None
    total = int(goals_home) + int(goals_away)
    return (total > 2) == is_over


def btts_win(goals_home, goals_away, is_yes: bool) -> Optional[bool]:
    if goals_home is None or goals_away is None:
        return None
    result = int(goals_home) > 0 and int(goals_away) > 0
    return result == is_yes


def ou15_win(goals_home, goals_away, is_over: bool) -> Optional[bool]:
    if goals_home is None or goals_away is None:
        return None
    total = int(goals_home) + int(goals_away)
    return (total > 1) == is_over


# ── Enumerate all candidate bets per fixture ──────────────────────────────────

def enumerate_candidates(pred: dict, window: str, dc_blend_w: float) -> List[dict]:
    """
    For one prediction record: yield all outcome/odds combinations.
    Each candidate has: market, outcome_name, odds, won (bool),
    league_id, date, window,
    ev_dc (EV using DC blend), ev_shin (EV using Shin-only = pure market),
    bootball_selected (would DC EV filter pick it?)
    """
    gh = pred.get("goals_home")
    ga = pred.get("goals_away")
    lid = pred.get("league_id")
    date = pred.get("date", "")
    dc = pred.get("dc", {})
    w = dc_blend_w

    candidates = []

    # h2h
    h2h_odds = [pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away")]
    h2h_p_dc = dc.get("p_h2h")
    if all(o is not None and o > 1.01 for o in h2h_odds) and h2h_p_dc:
        shin = shin_probabilities(h2h_odds)
        for idx, (name, o, p_dc, p_shin) in enumerate(
            zip(["H", "D", "A"], h2h_odds, h2h_p_dc, shin)
        ):
            won = h2h_win(gh, ga, idx)
            if won is None:
                continue
            p_blend = w * p_dc + (1 - w) * p_shin
            ev_dc = p_blend * o - 1.0
            ev_shin = p_shin * o - 1.0
            candidates.append({
                "market": "h2h", "outcome": name, "odds": o,
                "won": won, "pnl": (o - 1.0) if won else -1.0,
                "league_id": lid, "date": date, "window": window,
                "ev_dc": ev_dc, "ev_shin": ev_shin,
                "bootball_selected": (ev_dc > BOT_MIN_EV),
                "shin_selected": (ev_shin > BOT_MIN_EV),
            })

    # ou25
    o_ov, o_un = pred.get("odd_ou25_over"), pred.get("odd_ou25_under")
    p_dc_ov = dc.get("p_ou25_over")
    if o_ov and o_un and o_ov > 1.01 and o_un > 1.01 and p_dc_ov is not None:
        shin = shin_probabilities([o_ov, o_un])
        for name, o, is_over, p_dc, p_shin in [
            ("over",  o_ov, True,  p_dc_ov,       shin[0]),
            ("under", o_un, False, 1 - p_dc_ov,   shin[1]),
        ]:
            won = ou25_win(gh, ga, is_over)
            if won is None:
                continue
            p_blend = w * p_dc + (1 - w) * p_shin
            ev_dc = p_blend * o - 1.0
            ev_shin = p_shin * o - 1.0
            candidates.append({
                "market": "ou25", "outcome": name, "odds": o,
                "won": won, "pnl": (o - 1.0) if won else -1.0,
                "league_id": lid, "date": date, "window": window,
                "ev_dc": ev_dc, "ev_shin": ev_shin,
                "bootball_selected": (ev_dc > BOT_MIN_EV),
                "shin_selected": (ev_shin > BOT_MIN_EV),
            })

    # btts
    o_by, o_bn = pred.get("odd_btts_yes"), pred.get("odd_btts_no")
    p_dc_by = dc.get("p_btts_yes")
    if o_by and o_bn and o_by > 1.01 and o_bn > 1.01 and p_dc_by is not None:
        shin = shin_probabilities([o_by, o_bn])
        for name, o, is_yes, p_dc, p_shin in [
            ("yes", o_by, True,  p_dc_by,     shin[0]),
            ("no",  o_bn, False, 1 - p_dc_by, shin[1]),
        ]:
            won = btts_win(gh, ga, is_yes)
            if won is None:
                continue
            p_blend = w * p_dc + (1 - w) * p_shin
            ev_dc = p_blend * o - 1.0
            ev_shin = p_shin * o - 1.0
            candidates.append({
                "market": "btts", "outcome": name, "odds": o,
                "won": won, "pnl": (o - 1.0) if won else -1.0,
                "league_id": lid, "date": date, "window": window,
                "ev_dc": ev_dc, "ev_shin": ev_shin,
                "bootball_selected": (ev_dc > BOT_MIN_EV),
                "shin_selected": (ev_shin > BOT_MIN_EV),
            })

    # ou15
    o_15o, o_15u = pred.get("odd_ou15_over"), pred.get("odd_ou15_under")
    p_dc_15 = dc.get("p_ou15_over")
    if o_15o and o_15u and o_15o > 1.01 and o_15u > 1.01 and p_dc_15 is not None:
        shin = shin_probabilities([o_15o, o_15u])
        for name, o, is_over, p_dc, p_shin in [
            ("over",  o_15o, True,  p_dc_15,     shin[0]),
            ("under", o_15u, False, 1 - p_dc_15, shin[1]),
        ]:
            won = ou15_win(gh, ga, is_over)
            if won is None:
                continue
            p_blend = w * p_dc + (1 - w) * p_shin
            ev_dc = p_blend * o - 1.0
            ev_shin = p_shin * o - 1.0
            candidates.append({
                "market": "ou15", "outcome": name, "odds": o,
                "won": won, "pnl": (o - 1.0) if won else -1.0,
                "league_id": lid, "date": date, "window": window,
                "ev_dc": ev_dc, "ev_shin": ev_shin,
                "bootball_selected": (ev_dc > BOT_MIN_EV),
                "shin_selected": (ev_shin > BOT_MIN_EV),
            })

    return candidates


# ── Load all candidates ────────────────────────────────────────────────────────

def load_all_candidates() -> List[dict]:
    all_cands = []
    for w in WINDOWS:
        wname = w["name"]
        preds_path = CACHE_DIR / f"preds_{wname}.json"
        if not preds_path.exists():
            logger.warning(f"No preds cache for {wname}")
            continue
        preds = json.loads(preds_path.read_text())
        blend_w = DC_BLEND_W[wname]
        for pred in preds:
            all_cands.extend(enumerate_candidates(pred, wname, blend_w))
        logger.info(f"Window {wname}: {len(preds)} fixtures → {sum(1 for c in all_cands if c['window']==wname)} candidates")
    return all_cands


# ── Z1: Favorite-Longshot Bias ────────────────────────────────────────────────

def z1_flb(all_cands: List[dict]) -> dict:
    """
    Per odds bucket: naive ROI (all candidates) and Bootball concentration.
    Report separately for h2h and combined.
    """
    # All-market pooled bucket stats
    bucket_all: Dict[str, Dict] = {}
    # h2h-only bucket stats
    bucket_h2h: Dict[str, Dict] = {}
    # Bootball (DC EV-filtered) bets by bucket
    bucket_bot: Dict[str, Dict] = {}

    for c in all_cands:
        b = odds_bucket(c["odds"])

        for bucket_dict, cond in [
            (bucket_all, True),
            (bucket_h2h, c["market"] == "h2h"),
            (bucket_bot, c["bootball_selected"]),
        ]:
            if not cond:
                continue
            if b not in bucket_dict:
                bucket_dict[b] = {"pnls": [], "n": 0}
            bucket_dict[b]["pnls"].append(c["pnl"])
            bucket_dict[b]["n"] += 1

    def summarize(bd):
        rows = []
        total_n = sum(v["n"] for v in bd.values())
        for label, lo, hi in BUCKETS:
            v = bd.get(label, {"pnls": [], "n": 0})
            n = v["n"]
            if n == 0:
                rows.append({"bucket": label, "n": 0, "roi": None, "ci_lo": None,
                              "ci_hi": None, "share_pct": 0.0})
                continue
            roi = float(np.mean(v["pnls"]))
            ci = bootstrap_roi_ci(v["pnls"])
            rows.append({"bucket": label, "n": n, "roi": round(roi, 4),
                         "ci_lo": round(ci[0], 4), "ci_hi": round(ci[1], 4),
                         "share_pct": round(100.0 * n / max(total_n, 1), 1)})
        return rows

    return {
        "all_markets": summarize(bucket_all),
        "h2h": summarize(bucket_h2h),
        "bootball_bets": summarize(bucket_bot),
    }


# ── Z2: League Liquidity ──────────────────────────────────────────────────────

def z2_liquidity(all_cands: List[dict]) -> dict:
    """
    Bucket by league tier: naive market ROI and Bootball ROI per tier.
    """
    naive: Dict[str, List[float]] = defaultdict(list)
    bot:   Dict[str, List[float]] = defaultdict(list)

    for c in all_cands:
        tier = league_tier(c["league_id"])
        naive[tier].append(c["pnl"])
        if c["bootball_selected"]:
            bot[tier].append(c["pnl"])

    tiers = ["tier1_top", "tier2_mid", "tier3_longtail"]
    labels = {
        "tier1_top":      "Top fdco (EPL, La Liga, Serie A)",
        "tier2_mid":      "Mid fdco (Championship, L1/L2, Serie B, Segunda)",
        "tier3_longtail": "Long tail (all other leagues)",
    }
    rows = []
    for tier in tiers:
        naive_pnls = naive.get(tier, [])
        bot_pnls   = bot.get(tier, [])
        row = {
            "tier": tier,
            "label": labels[tier],
            "n_naive": len(naive_pnls),
            "roi_naive": round(float(np.mean(naive_pnls)), 4) if naive_pnls else None,
            "n_bot": len(bot_pnls),
            "roi_bot": round(float(np.mean(bot_pnls)), 4) if bot_pnls else None,
        }
        if len(naive_pnls) >= 30:
            ci = bootstrap_roi_ci(naive_pnls, n=2000)
            row["naive_ci_lo"] = round(ci[0], 4)
            row["naive_ci_hi"] = round(ci[1], 4)
        if bot_pnls:
            ci = bootstrap_roi_ci(bot_pnls, n=2000)
            row["bot_ci_lo"] = round(ci[0], 4)
            row["bot_ci_hi"] = round(ci[1], 4)
        rows.append(row)

    # Also break out by market and tier
    by_market: Dict[str, Dict] = {}
    for market in ["h2h", "ou25"]:
        by_market[market] = {}
        for tier in tiers:
            pnls = [c["pnl"] for c in all_cands if c["market"] == market and league_tier(c["league_id"]) == tier]
            if not pnls:
                by_market[market][tier] = {"n": 0}
                continue
            roi = float(np.mean(pnls))
            ci  = bootstrap_roi_ci(pnls, n=2000) if len(pnls) >= 30 else (None, None)
            by_market[market][tier] = {
                "n": len(pnls), "roi": round(roi, 4),
                "ci_lo": round(ci[0], 4) if ci[0] is not None else None,
                "ci_hi": round(ci[1], 4) if ci[1] is not None else None,
            }

    return {"tiers": rows, "by_market": by_market}


# ── Z3: Naive Contrarian Walk-Forward ────────────────────────────────────────

def z3_contrarian(all_cands: List[dict]) -> dict:
    """
    Walk-forward naive contrarian: bet on outcomes with odds <= threshold.
    No model, no calibration — pure market odds filter.
    Tests whether the favorite-longshot bias alone is exploitable.
    Per-window and aggregate ROI/CI.
    """
    THRESHOLDS  = [1.5, 2.0, 2.5]
    # Report h2h and ou25 separately (btts/ou15: single-window coverage)
    MARKETS     = ["h2h", "ou25"]
    WINDOW_NAMES = ["2022", "2023", "2025-26"]

    results = {}
    for market in MARKETS:
        results[market] = {}
        for thresh in THRESHOLDS:
            key = f"odds_le_{thresh}"
            per_window = {}
            all_pnls = []
            for wname in WINDOW_NAMES:
                pnls = [
                    c["pnl"] for c in all_cands
                    if c["market"] == market and c["window"] == wname and c["odds"] <= thresh
                ]
                if not pnls:
                    per_window[wname] = {"n": 0}
                    continue
                roi = float(np.mean(pnls))
                ci  = bootstrap_roi_ci(pnls)
                per_window[wname] = {
                    "n": len(pnls),
                    "roi": round(roi, 4),
                    "ci_lo": round(ci[0], 4),
                    "ci_hi": round(ci[1], 4),
                    "pass_500": len(pnls) >= 500,
                    "ci_excl_zero": ci[0] > 0,
                }
                all_pnls.extend(pnls)

            agg_roi = float(np.mean(all_pnls)) if all_pnls else None
            agg_ci  = bootstrap_roi_ci(all_pnls) if all_pnls else (None, None)
            n_windows_pass = sum(
                1 for v in per_window.values()
                if v.get("pass_500") and v.get("ci_excl_zero")
            )

            results[market][key] = {
                "threshold": thresh,
                "per_window": per_window,
                "aggregate": {
                    "n": len(all_pnls),
                    "roi": round(agg_roi, 4) if agg_roi is not None else None,
                    "ci_lo": round(agg_ci[0], 4) if agg_ci[0] is not None else None,
                    "ci_hi": round(agg_ci[1], 4) if agg_ci[1] is not None else None,
                },
                "n_windows_pass": n_windows_pass,
                "bar_met": n_windows_pass >= 2,
            }

    # Also: what does "bet EVERYTHING flat" look like (all odds, all markets)?
    all_pnls = [c["pnl"] for c in all_cands]
    roi_all = float(np.mean(all_pnls)) if all_pnls else None
    ci_all  = bootstrap_roi_ci(all_pnls, n=1000) if all_pnls else (None, None)
    results["all_markets_all_odds"] = {
        "n": len(all_pnls),
        "roi": round(roi_all, 4) if roi_all is not None else None,
        "ci_lo": round(ci_all[0], 4) if ci_all[0] is not None else None,
        "ci_hi": round(ci_all[1], 4) if ci_all[1] is not None else None,
    }

    return results


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(z1: dict, z2: dict, z3: dict, all_cands: List[dict]) -> None:
    lines = []
    lines.append("# Phase 4b — Bookmaker Bias Hunt\n\n")
    lines.append(f"**Validation pool:** {len(set(c['window']+'_'+str(c.get('league_id','')) for c in all_cands))} league-window pairs, "
                 f"{len(all_cands):,} total candidate bets across {len(set(c['window'] for c in all_cands))} walk-forward windows.\n\n")

    # ── Z1 ───────────────────────────────────────────────────────────────────
    lines.append("## Z1: Favorite-Longshot Bias\n\n")
    lines.append("### Z1a: All markets — naive flat-stake ROI by odds bucket\n\n")
    lines.append("*(All outcomes with odds available, no filter, unit stake — this tests whether the bias exists in this dataset.)*\n\n")
    lines.append("| Odds range | N bets | Naive ROI | 95% CI | Share of all candidates |\n")
    lines.append("|------------|--------|-----------|--------|-------------------------|\n")
    for row in z1["all_markets"]:
        if row["roi"] is None:
            lines.append(f"| {row['bucket']} | 0 | — | — | 0% |\n")
        else:
            lines.append(f"| {row['bucket']} | {row['n']:,} | {row['roi']:+.1%} | [{row['ci_lo']:+.1%}, {row['ci_hi']:+.1%}] | {row['share_pct']:.1f}% |\n")
    lines.append("\n")

    lines.append("### Z1b: h2h only — naive ROI by bucket\n\n")
    lines.append("| Odds range | N bets | Naive ROI | 95% CI |\n")
    lines.append("|------------|--------|-----------|--------|\n")
    for row in z1["h2h"]:
        if row["roi"] is None:
            lines.append(f"| {row['bucket']} | 0 | — | — |\n")
        else:
            lines.append(f"| {row['bucket']} | {row['n']:,} | {row['roi']:+.1%} | [{row['ci_lo']:+.1%}, {row['ci_hi']:+.1%}] |\n")
    lines.append("\n")

    lines.append("### Z1c: Bootball (DC EV-filtered) bet concentration by odds bucket\n\n")
    lines.append("*(Confirms where the EV filter actually placed bets — tests the longshot-trap hypothesis.)*\n\n")
    lines.append("| Odds range | N bets selected | Share of Bootball bets | Naive ROI at these odds |\n")
    lines.append("|------------|----------------|------------------------|-------------------------|\n")
    all_market_by_bucket = {r["bucket"]: r for r in z1["all_markets"]}
    for row in z1["bootball_bets"]:
        naive_roi = all_market_by_bucket.get(row["bucket"], {}).get("roi")
        naive_str = f"{naive_roi:+.1%}" if naive_roi is not None else "—"
        if row["n"] == 0:
            lines.append(f"| {row['bucket']} | 0 | 0% | {naive_str} |\n")
        else:
            lines.append(f"| {row['bucket']} | {row['n']:,} | {row['share_pct']:.1f}% | {naive_str} |\n")
    lines.append("\n")

    # Production context
    lines.append("*Production placed bets (448 settled): h2h avg odds 6.43 (72 bets, win rate 20.8%), "
                 "ou25 avg odds 2.75 (151 bets, win rate 38.4%), "
                 "btts avg odds 2.28 (163 bets, win rate 45.4%), "
                 "ou15 avg odds 3.26 (62 bets, win rate 40.3%).*\n\n")

    # ── Z2 ───────────────────────────────────────────────────────────────────
    lines.append("## Z2: League Liquidity Segmentation\n\n")
    lines.append("**Tier definitions:**\n")
    lines.append("- Tier 1 (top): EPL (39), La Liga (140), Serie A (135)\n")
    lines.append("- Tier 2 (mid fdco): Championship (40), League One (41), League Two (42), Serie B (136), Segunda División (141)\n")
    lines.append("- Tier 3 (long tail): all other leagues (fdco 2022/23 has no long-tail fixtures; "
                 "these appear only in the 2025-26 production window)\n\n")
    lines.append("### Z2a: All-market naive ROI by league tier\n\n")
    lines.append("| Tier | N candidates | Naive ROI | 95% CI | N Bootball bets | Bootball ROI | Bootball CI |\n")
    lines.append("|------|-------------|-----------|--------|-----------------|--------------|-------------|\n")
    for row in z2["tiers"]:
        ci_str = f"[{row.get('naive_ci_lo',0):+.1%}, {row.get('naive_ci_hi',0):+.1%}]" if row.get("naive_ci_lo") is not None else "—"
        bot_ci_str = f"[{row.get('bot_ci_lo',0):+.1%}, {row.get('bot_ci_hi',0):+.1%}]" if row.get("bot_ci_lo") is not None else "—"
        roi_str = f"{row['roi_naive']:+.1%}" if row["roi_naive"] is not None else "—"
        bot_roi_str = f"{row['roi_bot']:+.1%}" if row["roi_bot"] is not None else "—"
        lines.append(f"| {row['label']} | {row['n_naive']:,} | {roi_str} | {ci_str} | {row['n_bot']} | {bot_roi_str} | {bot_ci_str} |\n")
    lines.append("\n")

    lines.append("### Z2b: By market\n\n")
    lines.append("| Market | Tier | N | Naive ROI | 95% CI |\n")
    lines.append("|--------|------|---|-----------|--------|\n")
    tier_labels = {
        "tier1_top": "Tier 1 (top)", "tier2_mid": "Tier 2 (mid)",
        "tier3_longtail": "Tier 3 (long tail)"
    }
    for market in ["h2h", "ou25"]:
        for tier in ["tier1_top", "tier2_mid", "tier3_longtail"]:
            v = z2["by_market"][market].get(tier, {})
            if not v.get("n"):
                lines.append(f"| {market} | {tier_labels[tier]} | 0 | — | — |\n")
            else:
                ci_str = f"[{v['ci_lo']:+.1%}, {v['ci_hi']:+.1%}]" if v.get("ci_lo") is not None else "—"
                lines.append(f"| {market} | {tier_labels[tier]} | {v['n']:,} | {v['roi']:+.1%} | {ci_str} |\n")
    lines.append("\n")

    # ── Z3 ───────────────────────────────────────────────────────────────────
    lines.append("## Z3: Naive Contrarian Walk-Forward Backtest\n\n")
    lines.append("Strategy: flat-stake bet on **all outcomes where decimal odds ≤ threshold**, "
                 "no model, no calibration, same three walk-forward windows.\n\n")
    lines.append("*Pre-registered bar applies: 95% CI excludes zero (positive), ≥500 bets per window, ≥2 non-overlapping windows.*\n\n")

    for market in ["h2h", "ou25"]:
        lines.append(f"### Z3 — {market.upper()}\n\n")
        lines.append("| Threshold | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |\n")
        lines.append("|-----------|--------|--------|-----|--------|-------|-------|-------|\n")
        for thresh in [1.5, 2.0, 2.5]:
            key = f"odds_le_{thresh}"
            r = z3[market][key]
            for wname in ["2022", "2023", "2025-26"]:
                wv = r["per_window"].get(wname, {"n": 0})
                n = wv.get("n", 0)
                if n == 0:
                    lines.append(f"| ≤{thresh} | {wname} | 0 | — | — | NO | NO | FAIL |\n")
                else:
                    p500 = "YES" if wv.get("pass_500") else "NO"
                    ci0  = "YES" if wv.get("ci_excl_zero") else "NO"
                    pf   = "PASS" if (wv.get("pass_500") and wv.get("ci_excl_zero")) else "FAIL"
                    lines.append(f"| ≤{thresh} | {wname} | {n:,} | {wv['roi']:+.1%} | "
                                  f"[{wv['ci_lo']:+.1%}, {wv['ci_hi']:+.1%}] | {p500} | {ci0} | {pf} |\n")
            # summary
            agg = r["aggregate"]
            lines.append(f"| ≤{thresh} | **ALL** | {agg['n']:,} | {agg['roi']:+.1%} | "
                          f"[{agg['ci_lo']:+.1%}, {agg['ci_hi']:+.1%}] | — | — | "
                          f"{'**BAR MET**' if r['bar_met'] else 'BAR NOT MET'} |\n")
            lines.append("| | | | | | | | |\n")
        lines.append("\n")

    # ── Z4 ───────────────────────────────────────────────────────────────────
    lines.append("## Z4: Line-Timing Data Limitation\n\n")
    lines.append("Closing-line-value (CLV) and line-movement analysis cannot currently be tested. "
                 "The project has a single odds snapshot per fixture — either at ingestion time or "
                 "whenever the odds poller last captured them — not a time series of how each line "
                 "moved from opening to kickoff.\n\n")
    lines.append("The CLV infrastructure added in migration 024 (`placed_bets.closing_odds`, "
                 "`placed_bets.clv_pct`, captured by `odds_poll.py::capture_closing_lines`) will "
                 "collect near-kickoff snapshots going forward, but the historical backtest window "
                 "(2019–2026) has no multi-snapshot odds history. Testing timing-based edge will "
                 "require forward accumulation of live CLV data — a minimum of one full season of "
                 "active betting before any meaningful signal can be assessed.\n\n")
    lines.append("This limitation is flagged; no timing analysis has been attempted.\n")

    REPORT.write_text("".join(lines))
    logger.info(f"Report written to {REPORT}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("Loading all candidates from DC prediction cache...")
    all_cands = load_all_candidates()
    logger.info(f"Total candidate bets: {len(all_cands):,}")

    logger.info("Z1: Favorite-longshot bias...")
    z1 = z1_flb(all_cands)

    logger.info("Z2: League liquidity segmentation...")
    z2 = z2_liquidity(all_cands)

    logger.info("Z3: Naive contrarian walk-forward...")
    z3 = z3_contrarian(all_cands)

    logger.info("Writing results JSON...")
    RESULTS.write_text(json.dumps({"z1": z1, "z2": z2, "z3": z3}, indent=2))

    logger.info("Writing report...")
    write_report(z1, z2, z3, all_cands)

    logger.info("Done.")


if __name__ == "__main__":
    main()
