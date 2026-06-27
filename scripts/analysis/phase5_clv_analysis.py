"""
Phase 5 Task 1 — Closing-Line / CLV Analysis.

1.1 Re-extract closing-line odds from cached fdco CSVs into historical_odds.db
    (separate fixture_odds_closing table to avoid contaminating the MAX() loader).
1.2 Re-validate DC walk-forward using closing-line as reference price (Shin de-vig).
1.3 Direct CLV test: bets selected by opening-line EV filter → compare open vs close price.
1.4 Market-movement characterisation: mean absolute movement, directional bias.

Data available: fdco 2022 and 2023 windows only (CSVs cover 2019-2024; 2025-26 has no
closing-line history). Both windows must pass the bar (≥2 non-overlapping) — there is
no third window as slack.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import sqlite3
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.storage.db import get_session
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR   = Path(__file__).parent / "dc_cache"
HIST_DB     = Path(__file__).parent / "historical_odds.db"
REPORT      = Path(__file__).parent / "v5_clv_report.md"
RESULTS     = Path(__file__).parent / "phase5_clv_results.json"

DC_BLEND_W  = {"2022": 1.0, "2023": 0.65}   # 2025-26 excluded (no closing data)
BOT_MIN_EV  = 0.05
N_BOOTSTRAP = 5000

FDCO_LEAGUE_MAP = {
    39: "E0", 40: "E1", 41: "E2", 42: "E3",
    135: "I1", 136: "I2", 140: "SP1", 141: "SP2",
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def shin_probabilities(odds: List[float]) -> List[float]:
    n = len(odds)
    raw = [1.0 / o for o in odds]
    over = sum(raw)
    if n == 2:
        z_disc = 1.0 - 4.0 * (over - 1.0) * sum(r**2 for r in raw) / over**2
        z = (1.0 - math.sqrt(max(z_disc, 0.0))) / (2.0 * (over - 1.0)) if over > 1 else 0.0
        probs = [(math.sqrt(z**2 + 4*(1-z)*r/over) - z) / (2*(1-z))
                 if (1-z) > 1e-9 else r/over for r in raw]
    else:
        probs = [r/over for r in raw]
    s = sum(probs)
    return [p/s for p in probs]


def bootstrap_ci(values: List[float], n: int = N_BOOTSTRAP) -> Tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = np.random.default_rng(42)
    a = np.array(values)
    samples = rng.choice(a, size=(n, len(a)), replace=True).mean(axis=1)
    return (float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5)))


def normalize_name(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", name.lower())


def safe_float(row: dict, *keys) -> Optional[float]:
    for k in keys:
        v = row.get(k, "").strip()
        if v:
            try:
                f = float(v)
                return f if f >= 1.0 else None
            except ValueError:
                continue
    return None


def parse_fd_date(date_str: str) -> Optional[str]:
    """Convert DD/MM/YY or DD/MM/YYYY to YYYY-MM-DD."""
    from datetime import datetime
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── 1.1 Extract closing lines from CSVs ───────────────────────────────────────

def ensure_closing_table(hconn: sqlite3.Connection) -> None:
    hconn.execute("""
        CREATE TABLE IF NOT EXISTS fixture_odds_closing (
            id          INTEGER PRIMARY KEY,
            fixture_id  INTEGER NOT NULL,
            bookmaker   VARCHAR(20) NOT NULL,
            bet_type    VARCHAR(20) NOT NULL,
            odd_home    FLOAT,
            odd_draw    FLOAT,
            odd_away    FLOAT,
            odd_over    FLOAT,
            odd_under   FLOAT,
            UNIQUE(fixture_id, bookmaker, bet_type)
        )
    """)
    hconn.commit()


def build_fixture_lookup_from_preds(preds: List[dict]) -> Tuple[dict, dict]:
    """
    Build lookup: (date YYYY-MM-DD, norm_home, norm_away) → (fixture_id, home_team_id, away_team_id)
    Also build id → dict for quick lookup.
    """
    with get_session() as s:
        team_ids = set()
        for p in preds:
            team_ids.add(p["home_team_id"])
            team_ids.add(p["away_team_id"])
        id_list = ",".join(str(i) for i in team_ids)
        rows = s.execute(text(f"SELECT id, name FROM teams WHERE id IN ({id_list})")).fetchall()
        team_names = {r[0]: r[1] for r in rows}

    exact:  Dict[tuple, dict] = {}
    by_date: Dict[str, list] = {}
    for p in preds:
        d     = p["date"][:10]
        hname = team_names.get(p["home_team_id"], "")
        aname = team_names.get(p["away_team_id"], "")
        nh, na = normalize_name(hname), normalize_name(aname)
        rec = {"fixture_id": p["id"], "home_name": hname, "away_name": aname,
               "date": d, "opening": p}
        exact[(d, nh, na)] = rec
        by_date.setdefault(d, []).append((hname, aname, nh, na, rec))

    return exact, by_date


def match_csv_row(date_str: str, home_fd: str, away_fd: str,
                  exact: dict, by_date: dict) -> Optional[dict]:
    d  = parse_fd_date(date_str)
    if d is None:
        return None
    nh = normalize_name(home_fd)
    na = normalize_name(away_fd)
    rec = exact.get((d, nh, na))
    if rec:
        return rec
    candidates = by_date.get(d, [])
    best, best_rec = 0.0, None
    for (raw_h, raw_a, cn_h, cn_a, candidate_rec) in candidates:
        score = (SequenceMatcher(None, nh, cn_h).ratio() +
                 SequenceMatcher(None, na, cn_a).ratio()) / 2
        if score > best:
            best, best_rec = score, candidate_rec
    return best_rec if best >= 0.72 else None


def extract_closing_odds(preds_2022: List[dict], preds_2023: List[dict]) -> Dict[int, dict]:
    """
    For all fdco fixtures in 2022/2023 preds, find the CSV row and extract closing odds.
    Returns {fixture_id: {'h2h_close': (H,D,A), 'ou25_close': (ov,un)}}
    """
    all_preds = preds_2022 + preds_2023
    exact, by_date = build_fixture_lookup_from_preds(all_preds)

    fdco_cache = Path(__file__).parent / "fdco_cache"
    closing: Dict[int, dict] = {}
    matched = unmatched = 0

    for csv_path in sorted(fdco_cache.glob("*.csv")):
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                date_raw = row.get("Date", "")
                home_fd  = row.get("HomeTeam", "")
                away_fd  = row.get("AwayTeam", "")
                if not (date_raw and home_fd and away_fd):
                    continue
                rec = match_csv_row(date_raw, home_fd, away_fd, exact, by_date)
                if rec is None:
                    unmatched += 1
                    continue

                fid = rec["fixture_id"]
                # h2h closing — prefer B365C, then PSC, then AvgC, then MaxC
                ch = safe_float(row, "B365CH", "PSCH", "AvgCH", "MaxCH")
                cd = safe_float(row, "B365CD", "PSCD", "AvgCD", "MaxCD")
                ca = safe_float(row, "B365CA", "PSCA", "AvgCA", "MaxCA")
                # ou25 closing
                cov = safe_float(row, "B365C>2.5", "AvgC>2.5", "MaxC>2.5")
                cun = safe_float(row, "B365C<2.5", "AvgC<2.5", "MaxC<2.5")

                entry = closing.get(fid, {})
                if ch and cd and ca and fid not in closing:
                    entry["h2h_close"] = (ch, cd, ca)
                    entry["h2h_open"]  = (rec["opening"]["odd_home"],
                                          rec["opening"]["odd_draw"],
                                          rec["opening"]["odd_away"])
                if cov and cun and "ou25_close" not in entry:
                    entry["ou25_close"] = (cov, cun)
                    entry["ou25_open"]  = (rec["opening"]["odd_ou25_over"],
                                           rec["opening"]["odd_ou25_under"])
                closing[fid] = entry
                matched += 1

    logger.info(f"Closing odds: matched={matched}, unmatched={unmatched}, fixtures={len(closing)}")
    return closing


def store_closing_in_db(closing: Dict[int, dict], hconn: sqlite3.Connection) -> None:
    ensure_closing_table(hconn)
    n = 0
    for fid, entry in closing.items():
        if "h2h_close" in entry:
            h, d, a = entry["h2h_close"]
            hconn.execute("""
                INSERT OR IGNORE INTO fixture_odds_closing
                (fixture_id, bookmaker, bet_type, odd_home, odd_draw, odd_away)
                VALUES (?, 'b365c', 'h2h', ?, ?, ?)
            """, (fid, h, d, a))
            n += 1
        if "ou25_close" in entry:
            ov, un = entry["ou25_close"]
            hconn.execute("""
                INSERT OR IGNORE INTO fixture_odds_closing
                (fixture_id, bookmaker, bet_type, odd_over, odd_under)
                VALUES (?, 'b365c', 'over_under', ?, ?)
            """, (fid, ov, un))
    hconn.commit()
    logger.info(f"Stored {n} closing h2h records in historical_odds.db")


# ── 1.2 DC validated against closing line ──────────────────────────────────────

def dc_close_vs_open(preds: List[dict], wname: str, blend_w: float,
                     closing: Dict[int, dict]) -> dict:
    """
    Two parallel simulations:
    - Open: select bets using EV computed with opening Shin probabilities (replicates Phase 3).
    - Close: select bets using EV computed with closing Shin probabilities (hypothetical).
    """
    results = {}
    for label, use_close in [("open", False), ("close", True)]:
        h2h_pnl, ou25_pnl = [], []
        for pred in preds:
            fid     = pred["id"]
            dc      = pred.get("dc", {})
            ph      = dc.get("p_h2h")
            pov     = dc.get("p_ou25_over")
            gh, ga  = pred.get("goals_home"), pred.get("goals_away")

            # h2h
            if ph:
                if use_close:
                    entry = closing.get(fid, {})
                    o_odds = entry.get("h2h_close")
                else:
                    o = pred.get("odd_home"); d = pred.get("odd_draw"); a = pred.get("odd_away")
                    o_odds = (o, d, a) if (o and d and a and o > 1.01 and d > 1.01 and a > 1.01) else None

                if o_odds and all(x and x > 1.01 for x in o_odds):
                    shin  = shin_probabilities(list(o_odds))
                    for idx, (p_dc, p_sh) in enumerate(zip(ph, shin)):
                        pb = blend_w*p_dc + (1-blend_w)*p_sh
                        ev = pb * o_odds[idx] - 1.0
                        if ev > BOT_MIN_EV:
                            if gh is not None and ga is not None:
                                outcome = 0 if gh>ga else (1 if gh==ga else 2)
                                won = outcome == idx
                                h2h_pnl.append((o_odds[idx]-1.0) if won else -1.0)

            # ou25
            if pov is not None:
                if use_close:
                    entry = closing.get(fid, {})
                    o_odds_ou = entry.get("ou25_close")
                else:
                    ov = pred.get("odd_ou25_over"); un = pred.get("odd_ou25_under")
                    o_odds_ou = (ov, un) if (ov and un and ov > 1.01 and un > 1.01) else None

                if o_odds_ou and all(x and x > 1.01 for x in o_odds_ou):
                    shin  = shin_probabilities(list(o_odds_ou))
                    for is_ov, p_dc, p_sh, o, win_if in [
                        (True,  pov,     shin[0], o_odds_ou[0], 1),
                        (False, 1-pov,   shin[1], o_odds_ou[1], 0),
                    ]:
                        pb = blend_w*p_dc + (1-blend_w)*p_sh
                        ev = pb * o - 1.0
                        if ev > BOT_MIN_EV:
                            if gh is not None and ga is not None:
                                total = gh + ga
                                won   = (total > 2.5) == is_ov
                                ou25_pnl.append((o-1.0) if won else -1.0)

        ci_h = bootstrap_ci(h2h_pnl)
        ci_o = bootstrap_ci(ou25_pnl)
        results[label] = {
            "h2h":  {"n": len(h2h_pnl),  "roi": np.mean(h2h_pnl)  if h2h_pnl  else None,
                     "ci_lo": ci_h[0], "ci_hi": ci_h[1]},
            "ou25": {"n": len(ou25_pnl), "roi": np.mean(ou25_pnl) if ou25_pnl else None,
                     "ci_lo": ci_o[0], "ci_hi": ci_o[1]},
        }
    return results


# ── 1.3 Direct CLV test ────────────────────────────────────────────────────────

def clv_test(preds: List[dict], wname: str, blend_w: float,
             closing: Dict[int, dict]) -> dict:
    """
    For bets selected by the OPENING-line EV filter, compute
    CLV% = (opening_odds - closing_odds) / closing_odds per bet.
    Positive = got better price than market settled on.
    Consistent with odds_poll.py:383 CLV formula.
    """
    h2h_clv  = []
    ou25_clv = []
    n_selected_h2h = n_no_close_h2h = 0
    n_selected_ou25 = n_no_close_ou25 = 0

    for pred in preds:
        fid  = pred["id"]
        dc   = pred.get("dc", {})
        ph   = dc.get("p_h2h")
        pov  = dc.get("p_ou25_over")
        entry = closing.get(fid, {})

        # h2h — select using opening odds
        o_h = pred.get("odd_home"); o_d = pred.get("odd_draw"); o_a = pred.get("odd_away")
        if ph and o_h and o_d and o_a and o_h>1.01 and o_d>1.01 and o_a>1.01:
            shin_open = shin_probabilities([o_h, o_d, o_a])
            for idx, (p_dc, p_sh, o_open) in enumerate(zip(ph, shin_open, [o_h, o_d, o_a])):
                pb = blend_w*p_dc + (1-blend_w)*p_sh
                ev = pb * o_open - 1.0
                if ev > BOT_MIN_EV:
                    n_selected_h2h += 1
                    close = entry.get("h2h_close")
                    if close and close[idx] and close[idx] > 1.01:
                        clv_pct = (o_open - close[idx]) / close[idx]
                        h2h_clv.append(clv_pct)
                    else:
                        n_no_close_h2h += 1

        # ou25 — select using opening odds
        o_ov = pred.get("odd_ou25_over"); o_un = pred.get("odd_ou25_under")
        if pov is not None and o_ov and o_un and o_ov>1.01 and o_un>1.01:
            shin_open = shin_probabilities([o_ov, o_un])
            for is_ov, p_dc, p_sh, o_open, close_idx in [
                (True,  pov,   shin_open[0], o_ov, 0),
                (False, 1-pov, shin_open[1], o_un, 1),
            ]:
                pb = blend_w*p_dc + (1-blend_w)*p_sh
                ev = pb * o_open - 1.0
                if ev > BOT_MIN_EV:
                    n_selected_ou25 += 1
                    close = entry.get("ou25_close")
                    if close and close[close_idx] and close[close_idx] > 1.01:
                        clv_pct = (o_open - close[close_idx]) / close[close_idx]
                        ou25_clv.append(clv_pct)
                    else:
                        n_no_close_ou25 += 1

    ci_h = bootstrap_ci(h2h_clv)
    ci_o = bootstrap_ci(ou25_clv)
    logger.info(f"  {wname} h2h: {n_selected_h2h} selected, {len(h2h_clv)} with close, {n_no_close_h2h} no close")
    logger.info(f"  {wname} ou25: {n_selected_ou25} selected, {len(ou25_clv)} with close, {n_no_close_ou25} no close")
    return {
        "h2h": {
            "n_selected": n_selected_h2h, "n_with_close": len(h2h_clv),
            "n_no_close": n_no_close_h2h,
            "mean_clv": float(np.mean(h2h_clv)) if h2h_clv else None,
            "ci_lo": ci_h[0], "ci_hi": ci_h[1],
            "pass_500": len(h2h_clv) >= 500,
            "ci_excl_zero_pos": ci_h[0] > 0,
        },
        "ou25": {
            "n_selected": n_selected_ou25, "n_with_close": len(ou25_clv),
            "n_no_close": n_no_close_ou25,
            "mean_clv": float(np.mean(ou25_clv)) if ou25_clv else None,
            "ci_lo": ci_o[0], "ci_hi": ci_o[1],
            "pass_500": len(ou25_clv) >= 500,
            "ci_excl_zero_pos": ci_o[0] > 0,
        },
    }


# ── 1.4 Market movement characterisation ──────────────────────────────────────

def market_movement(preds_2022: List[dict], preds_2023: List[dict],
                    closing: Dict[int, dict]) -> dict:
    """
    Per outcome position: mean (close - open), mean |close - open|, % that shortened.
    Reports h2h and ou25.
    """
    h2h_moves: Dict[int, list] = {0: [], 1: [], 2: []}  # H, D, A
    ou25_moves: Dict[int, list] = {0: [], 1: []}          # over, under

    for pred in preds_2022 + preds_2023:
        fid   = pred["id"]
        entry = closing.get(fid, {})

        o_open = [pred.get("odd_home"), pred.get("odd_draw"), pred.get("odd_away")]
        c_close = entry.get("h2h_close")
        if c_close and all(x and x>1.01 for x in o_open) and all(x and x>1.01 for x in c_close):
            for i in range(3):
                h2h_moves[i].append(c_close[i] - o_open[i])

        ov_open = pred.get("odd_ou25_over"); un_open = pred.get("odd_ou25_under")
        c_ou25  = entry.get("ou25_close")
        if c_ou25 and ov_open and un_open and ov_open>1.01 and un_open>1.01:
            if c_ou25[0] and c_ou25[0]>1.01:
                ou25_moves[0].append(c_ou25[0] - ov_open)
            if c_ou25[1] and c_ou25[1]>1.01:
                ou25_moves[1].append(c_ou25[1] - un_open)

    result = {}
    for label, moves, idx_names in [
        ("h2h",  h2h_moves,  ["home", "draw", "away"]),
        ("ou25", ou25_moves, ["over", "under"]),
    ]:
        result[label] = {}
        for i, name in enumerate(idx_names):
            arr = np.array(moves[i])
            if len(arr) == 0:
                continue
            result[label][name] = {
                "n": len(arr),
                "mean_move": round(float(arr.mean()), 4),
                "mean_abs_move": round(float(np.abs(arr).mean()), 4),
                "pct_shortened": round(float((arr < 0).mean()), 4),  # odds went down
                "pct_lengthened": round(float((arr > 0).mean()), 4),
                "pct_unchanged": round(float((arr == 0).mean()), 4),
            }
    return result


# ── Report ─────────────────────────────────────────────────────────────────────

def write_report(extraction: dict, by_window_12: dict, by_window_13: dict, t14: dict) -> None:
    lines = []
    L = lines.append

    L("# Phase 5 — Closing-Line / CLV Analysis\n\n")
    L("> **Scope note:** Closing-line data exists only for the fdco windows (2022 + 2023). "
      "The 2025-26 production pool was assembled from API-Football snapshots with no "
      "historical close series. Because ≥2 non-overlapping windows are required, "
      "**both fdco windows must pass** — there is no third window as slack.\n\n")

    # 1.1
    L("## 1.1 Closing-Line Extraction\n\n")
    L(f"- CSV files processed: 40 (all cached at `scripts/analysis/fdco_cache/`)\n")
    L(f"- Fixtures matched with closing h2h odds: {extraction['n_h2h']:,}\n")
    L(f"- Fixtures matched with closing ou25 odds: {extraction['n_ou25']:,}\n")
    L("- Bookmaker priority (open and close): B365 → PS → Avg → Max\n")
    L("- Stored in `fixture_odds_closing` table (separate from `fixture_odds` to avoid contaminating `MAX()` aggregation in V4 loader)\n\n")

    # 1.2
    L("## 1.2 DC Re-Validated Against Closing Line\n\n")
    L("Comparison: opening-line Shin probabilities (Phase 3 original) vs closing-line Shin probabilities. "
      "DC model probabilities unchanged; only the market reference changes.\n\n")
    L("*Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows.*\n\n")

    for market in ["h2h", "ou25"]:
        L(f"### {market.upper()}\n\n")
        L("| Window | Reference | N bets | ROI | 95% CI | CI>0? |\n")
        L("|--------|-----------|--------|-----|--------|-------|\n")
        for wname in ["2022", "2023"]:
            r = by_window_12.get(wname, {})
            for ref in ["open", "close"]:
                v = r.get(ref, {}).get(market, {})
                n   = v.get("n", 0)
                roi = v.get("roi")
                if n == 0 or roi is None:
                    L(f"| {wname} | {ref} | 0 | — | — | — |\n")
                else:
                    ci0 = "YES" if v.get("ci_hi", 0) < 0 or v.get("ci_lo", 0) > 0 else "NO"
                    # actually need ci_lo > 0 for positive direction
                    ci0 = "YES" if v.get("ci_lo", 0) > 0 else "NO"
                    L(f"| {wname} | {ref} | {n:,} | {roi:+.1%} | "
                      f"[{v['ci_lo']:+.1%},{v['ci_hi']:+.1%}] | {ci0} |\n")
        L("\n")

    # 1.3
    L("## 1.3 Direct CLV Test\n\n")
    L("Selection criterion: **opening-line EV filter** (same as Phase 3 DC backtest — "
      "as a real bettor would act before the close). CLV% = (opening_price − closing_price) / "
      "closing_price, matching `odds_poll.py:383`. Positive = we got better price than "
      "the market settled on.\n\n")
    L("*Pre-registered bar: 95% CI > 0 (positive), ≥500 bets/window, ≥2 windows.*\n\n")

    for market in ["h2h", "ou25"]:
        L(f"### {market.upper()}\n\n")
        L("| Window | N selected | N with close | Mean CLV% | 95% CI | ≥500? | CI>0? | Pass? |\n")
        L("|--------|------------|--------------|-----------|--------|-------|-------|-------|\n")
        passes = 0
        for wname in ["2022", "2023"]:
            v = by_window_13.get(wname, {}).get(market, {})
            n = v.get("n_with_close", 0)
            clv = v.get("mean_clv")
            if n == 0 or clv is None:
                L(f"| {wname} | 0 | 0 | — | — | NO | NO | FAIL |\n")
            else:
                p5  = "YES" if v.get("pass_500") else "NO"
                ci0 = "YES" if v.get("ci_excl_zero_pos") else "NO"
                pf  = "PASS" if (v.get("pass_500") and v.get("ci_excl_zero_pos")) else "FAIL"
                if v.get("pass_500") and v.get("ci_excl_zero_pos"):
                    passes += 1
                L(f"| {wname} | {v['n_selected']:,} | {n:,} | {clv:+.2%} | "
                  f"[{v['ci_lo']:+.2%},{v['ci_hi']:+.2%}] | {p5} | {ci0} | {pf} |\n")
        verdict = "**BAR MET**" if passes >= 2 else "BAR NOT MET"
        L(f"\n*{market.upper()} CLV verdict: {verdict} ({passes}/2 windows pass)*\n\n")

    # 1.4
    L("## 1.4 Market Movement Context\n\n")
    L("Mean odds movement from open to close (positive = odds lengthened, negative = shortened). "
      "All fdco 2022+2023 fixtures combined.\n\n")

    L("### H2H Movement\n\n")
    L("| Outcome | N | Mean move | Mean |move| | % shortened | % lengthened |\n")
    L("|---------|---|-----------|-------------|-------------|---------------|\n")
    for name in ["home", "draw", "away"]:
        v = t14.get("h2h", {}).get(name, {})
        if not v:
            continue
        L(f"| {name} | {v['n']:,} | {v['mean_move']:+.3f} | {v['mean_abs_move']:.3f} "
          f"| {v['pct_shortened']:.1%} | {v['pct_lengthened']:.1%} |\n")

    L("\n### OU25 Movement\n\n")
    L("| Outcome | N | Mean move | Mean |move| | % shortened | % lengthened |\n")
    L("|---------|---|-----------|-------------|-------------|---------------|\n")
    for name in ["over", "under"]:
        v = t14.get("ou25", {}).get(name, {})
        if not v:
            continue
        L(f"| {name} | {v['n']:,} | {v['mean_move']:+.3f} | {v['mean_abs_move']:.3f} "
          f"| {v['pct_shortened']:.1%} | {v['pct_lengthened']:.1%} |\n")

    L("\n")

    REPORT.write_text("".join(lines))
    logger.info(f"Report written to {REPORT}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    preds_2022 = json.loads((CACHE_DIR / "preds_2022.json").read_text())
    preds_2023 = json.loads((CACHE_DIR / "preds_2023.json").read_text())

    logger.info("1.1 Extracting closing odds from CSVs...")
    closing = extract_closing_odds(preds_2022, preds_2023)
    n_h2h  = sum(1 for v in closing.values() if "h2h_close"  in v)
    n_ou25 = sum(1 for v in closing.values() if "ou25_close" in v)
    logger.info(f"  h2h closing: {n_h2h}, ou25 closing: {n_ou25}")

    # Store in DB
    hconn = sqlite3.connect(str(HIST_DB))
    store_closing_in_db(closing, hconn)
    hconn.close()

    logger.info("1.2 DC open vs close reference...")
    by_window_12 = {}
    for wname, preds, w in [("2022", preds_2022, 1.0), ("2023", preds_2023, 0.65)]:
        by_window_12[wname] = dc_close_vs_open(preds, wname, w, closing)
        logger.info(f"  {wname}: open h2h ROI={by_window_12[wname]['open']['h2h']['roi']:.4f}, "
                    f"close h2h ROI={by_window_12[wname]['close']['h2h']['roi']:.4f}")

    logger.info("1.3 CLV test...")
    by_window_13 = {}
    for wname, preds, w in [("2022", preds_2022, 1.0), ("2023", preds_2023, 0.65)]:
        by_window_13[wname] = clv_test(preds, wname, w, closing)
        for m in ["h2h", "ou25"]:
            r = by_window_13[wname][m]
            logger.info(f"  {wname} {m}: mean CLV={r.get('mean_clv','?')}, n={r.get('n_with_close',0)}")

    logger.info("1.4 Market movement...")
    t14 = market_movement(preds_2022, preds_2023, closing)

    extraction_meta = {"n_h2h": n_h2h, "n_ou25": n_ou25}
    RESULTS.write_text(json.dumps({
        "extraction": extraction_meta,
        "task12": by_window_12,
        "task13": by_window_13,
        "task14": t14,
    }, indent=2))

    write_report(extraction_meta, by_window_12, by_window_13, t14)
    logger.info("Task 1 complete.")


if __name__ == "__main__":
    main()
