"""
Phase 34 — First Look at the Odds-Trajectory Data (descriptive, no edge claims)

Active-all odds_snapshots capture (Phase 25) has run since 2026-07-02. This is the
FIRST analysis of that dataset. Scope discipline: 9 days of data supports DESCRIPTIVE
structure only -- how odds move, when books post, who moves first. No ROI backtests,
no pre-registered bars this phase.

Four tasks:
  Task 1: Data quality -- snapshots/fixture, capture-spacing vs design, per-bookmaker
          presence over the fixture lifecycle, July 2-8 scheduler-bug window check.
  Task 2: Movement structure -- magnitude, direction, timing, book-vs-book lead/lag.
  Task 3: Our predictions vs. the movement -- toward/away-from-us, "model high" cases.
  Task 4: Synthesis + pre-registered Phase 35 trigger (written by hand from this
          script's output, not generated here).

Read-only on data/football.db. Zero API calls.

Run:
    python3 scripts/analysis/phase34_odds_trajectory.py

Outputs:
    scripts/analysis/phase34_results.json
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB_MAIN = Path("/opt/projects/bootball/data/football.db")
OUT_JSON = Path(__file__).parent / "phase34_results.json"

SETTLED_STATUSES = {"FT", "AET", "PEN"}
EARLY_HOURS = 6.0   # matches odds_trajectory_scheduler.py's HOURLY_PHASE_HOURS
MARKETS = ["h2h", "ou25", "btts"]

# One tracked "side" per market for direction/magnitude bookkeeping -- keeps every
# fixture's movement comparable instead of switching frame depending on the favorite.
SIDE_COLS = {
    "h2h": [("home", "odd_home"), ("draw", "odd_draw"), ("away", "odd_away")],
    "ou25": [("over", "odd_over"), ("under", "odd_under")],
    "btts": [("yes", "odd_btts_yes"), ("no", "odd_btts_no")],
}


def load_snapshots(conn) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT s.id, s.fixture_id, s.bookmaker_name, s.market_type, s.captured_at,
               s.odd_home, s.odd_draw, s.odd_away, s.odd_over, s.odd_under,
               s.odd_btts_yes, s.odd_btts_no,
               f.date AS kickoff, f.status, f.league_id, f.outcome,
               f.goals_home, f.goals_away
        FROM odds_snapshots s
        JOIN fixtures f ON f.id = s.fixture_id
        """,
        conn, parse_dates=["captured_at", "kickoff"],
    )
    df["hours_to_ko"] = (df["kickoff"] - df["captured_at"]).dt.total_seconds() / 3600.0
    df["settled"] = df["status"].isin(SETTLED_STATUSES)
    df["capture_date"] = df["captured_at"].dt.date
    return df


def load_predictions(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT fixture_id, market, our_prob, calibrated_prob, served_prob,
               predicted_outcome, actual_outcome, won, settled, data_context
        FROM prediction_records
        WHERE settled = 1 AND our_prob IS NOT NULL
        """,
        conn,
    )


def load_fixtures(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT id, league_id, date, status FROM fixtures", conn, parse_dates=["date"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Data quality
# ─────────────────────────────────────────────────────────────────────────────

def task1_data_quality(snaps: pd.DataFrame) -> dict:
    out: dict = {}

    per_fix = snaps.groupby("fixture_id").size()
    out["snapshots_per_fixture"] = {
        "n_fixtures": int(per_fix.shape[0]),
        "mean": round(float(per_fix.mean()), 1),
        "median": float(per_fix.median()),
        "p10": float(per_fix.quantile(0.10)),
        "p25": float(per_fix.quantile(0.25)),
        "p75": float(per_fix.quantile(0.75)),
        "p90": float(per_fix.quantile(0.90)),
        "max": int(per_fix.max()),
        "n_with_1_snapshot": int((per_fix == 1).sum()),
        "n_with_ge2": int((per_fix >= 2).sum()),
    }

    settled = snaps[snaps["settled"]]
    per_fix_settled = settled.groupby("fixture_id").size()
    out["settled_snapshots_per_fixture"] = {
        "n_fixtures": int(per_fix_settled.shape[0]),
        "mean": round(float(per_fix_settled.mean()), 1) if len(per_fix_settled) else None,
        "median": float(per_fix_settled.median()) if len(per_fix_settled) else None,
    }

    has_early = settled[settled["hours_to_ko"] > EARLY_HOURS].groupby("fixture_id").size()
    has_near = settled[(settled["hours_to_ko"] <= EARLY_HOURS) & (settled["hours_to_ko"] >= 0)].groupby("fixture_id").size()
    qualifying = sorted(set(has_early.index) & set(has_near.index))
    out["qualifying_settled_fixtures"] = {
        "n": len(qualifying),
        "has_early_only_no_near": len(set(has_early.index) - set(has_near.index)),
        "has_near_only_no_early": len(set(has_near.index) - set(has_early.index)),
    }
    out["_qualifying_fixture_ids"] = qualifying  # consumed by tasks 2/3, stripped before json dump

    # Capture spacing vs design: successive inter-capture gaps, split by phase at
    # time of the EARLIER capture in each consecutive pair (per fixture+bookmaker+market).
    key_cols = ["fixture_id", "bookmaker_name", "market_type"]
    gaps_daily, gaps_hourly = [], []
    for _, g in snaps.sort_values("captured_at").groupby(key_cols):
        if len(g) < 2:
            continue
        times = g["captured_at"].values
        hrs = g["hours_to_ko"].values
        diffs_hours = np.diff(times).astype("timedelta64[s]").astype(float) / 3600.0
        for i, dh in enumerate(diffs_hours):
            if hrs[i] > EARLY_HOURS:
                gaps_daily.append(dh)
            else:
                gaps_hourly.append(dh)
    gaps_daily = np.array(gaps_daily)
    gaps_hourly = np.array(gaps_hourly)
    out["capture_spacing"] = {
        "daily_phase_design_hours": 24.0,
        "daily_phase_actual": {
            "n": int(len(gaps_daily)),
            "median_hours": round(float(np.median(gaps_daily)), 2) if len(gaps_daily) else None,
            "p25_hours": round(float(np.percentile(gaps_daily, 25)), 2) if len(gaps_daily) else None,
            "p75_hours": round(float(np.percentile(gaps_daily, 75)), 2) if len(gaps_daily) else None,
        },
        "hourly_phase_design_hours": 1.0,
        "hourly_phase_actual": {
            "n": int(len(gaps_hourly)),
            "median_hours": round(float(np.median(gaps_hourly)), 2) if len(gaps_hourly) else None,
            "p25_hours": round(float(np.percentile(gaps_hourly, 25)), 2) if len(gaps_hourly) else None,
            "p75_hours": round(float(np.percentile(gaps_hourly, 75)), 2) if len(gaps_hourly) else None,
        },
    }

    # Per-bookmaker presence over the fixture lifecycle -- bucket by hours-to-kickoff,
    # report % of (fixture, bucket) cells with a Pinnacle snapshot vs. the modal soft book.
    bins = [(-0.5, 1), (1, 2), (2, 6), (6, 24), (24, 72), (72, 168)]
    bin_labels = ["0-1h", "1-2h", "2-6h", "6-24h", "24-72h", "72-168h"]
    modal_soft_book = (
        snaps[snaps["bookmaker_name"] != "Pinnacle"]["bookmaker_name"].value_counts().idxmax()
    )
    presence_rows = []
    for lo, hi in bins:
        label = bin_labels[bins.index((lo, hi))]
        bucket = snaps[(snaps["hours_to_ko"] > lo) & (snaps["hours_to_ko"] <= hi)]
        n_fixtures_in_bucket = bucket["fixture_id"].nunique()
        n_pinnacle = bucket[bucket["bookmaker_name"] == "Pinnacle"]["fixture_id"].nunique()
        n_modal = bucket[bucket["bookmaker_name"] == modal_soft_book]["fixture_id"].nunique()
        presence_rows.append({
            "bucket": label,
            "n_fixtures_with_any_snapshot": int(n_fixtures_in_bucket),
            "pinnacle_pct": round(100 * n_pinnacle / n_fixtures_in_bucket, 1) if n_fixtures_in_bucket else None,
            f"{modal_soft_book}_pct": round(100 * n_modal / n_fixtures_in_bucket, 1) if n_fixtures_in_bucket else None,
        })
    out["bookmaker_presence_by_bucket"] = presence_rows
    out["modal_soft_book"] = modal_soft_book

    # Pinnacle x market coverage -- surfaced separately because it's a structural
    # per-market gap, not a lifecycle-timing effect the bucket table above would show.
    market_book_counts = (
        snaps.groupby(["market_type", "bookmaker_name"]).size().unstack(fill_value=0)
    )
    out["pinnacle_rows_by_market"] = {
        m: int(market_book_counts.loc[m, "Pinnacle"]) if "Pinnacle" in market_book_counts.columns and m in market_book_counts.index else 0
        for m in MARKETS
    }

    # Daily capture volume + candidate exclusion check (July 2-8 scheduler-bug window)
    daily = snaps.groupby("capture_date").agg(
        rows=("id", "size"), fixtures=("fixture_id", "nunique")
    )
    out["daily_capture_volume"] = {
        str(d): {"rows": int(r.rows), "fixtures": int(r.fixtures)} for d, r in daily.iterrows()
    }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Movement structure
# ─────────────────────────────────────────────────────────────────────────────

def _implied(odds: pd.Series) -> pd.Series:
    return 1.0 / odds


def _open_close_per_key(snaps: pd.DataFrame, market: str, side_col: str,
                          qualifying_ids: set[int]) -> pd.DataFrame:
    """Per (fixture, bookmaker), the earliest 'early' (>6h) and latest 'near' (<=6h,>=0)
    snapshot's implied probability for one side of one market. Only rows where both
    exist and the odds value is present."""
    d = snaps[
        (snaps["market_type"] == market)
        & (snaps["fixture_id"].isin(qualifying_ids))
        & snaps[side_col].notna()
    ].copy()
    d["implied"] = _implied(d[side_col])

    early = d[d["hours_to_ko"] > EARLY_HOURS].sort_values("captured_at")
    near = d[(d["hours_to_ko"] <= EARLY_HOURS) & (d["hours_to_ko"] >= 0)].sort_values("captured_at")

    open_ = early.groupby(["fixture_id", "bookmaker_name"]).first()[["implied", "captured_at", "hours_to_ko"]]
    close_ = near.groupby(["fixture_id", "bookmaker_name"]).last()[["implied", "captured_at", "hours_to_ko"]]

    merged = open_.join(close_, lsuffix="_open", rsuffix="_close", how="inner")
    merged = merged.reset_index()
    return merged


def task2_movement(snaps: pd.DataFrame, qualifying_ids: set[int]) -> dict:
    out: dict = {}

    # ── 2.1/2.2 Magnitude + direction, per market, per bookmaker ───────────────
    magnitude_rows = []
    direction_rows = []
    oc_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for market in MARKETS:
        for side_name, side_col in SIDE_COLS[market]:
            oc = _open_close_per_key(snaps, market, side_col, qualifying_ids)
            oc_cache[(market, side_name)] = oc
            if oc.empty:
                continue
            oc = oc.copy()
            oc["delta"] = oc["implied_close"] - oc["implied_open"]
            oc["abs_delta"] = oc["delta"].abs()
            for book, g in oc.groupby("bookmaker_name"):
                if len(g) < 5:
                    continue  # too thin per-book/side to report a distribution
                magnitude_rows.append({
                    "market": market, "side": side_name, "bookmaker": book, "n": int(len(g)),
                    "mean_abs_delta_pp": round(100 * g["abs_delta"].mean(), 2),
                    "median_abs_delta_pp": round(100 * g["abs_delta"].median(), 2),
                    "p75_abs_delta_pp": round(100 * g["abs_delta"].quantile(0.75), 2),
                    "mean_signed_delta_pp": round(100 * g["delta"].mean(), 2),
                })

        # direction: favorite (highest opening implied prob among the market's sides) —
        # does the favorite's own implied prob rise (shorten) or fall (drift) by close?
        per_side = {}
        for side_name, side_col in SIDE_COLS[market]:
            oc = oc_cache[(market, side_name)]
            if not oc.empty:
                per_side[side_name] = oc.set_index(["fixture_id", "bookmaker_name"])[["implied_open", "implied_close"]]
        if not per_side:
            continue
        # align on (fixture, bookmaker) present for ALL sides of this market so "favorite" is well-defined
        common_idx = None
        for v in per_side.values():
            common_idx = v.index if common_idx is None else common_idx.intersection(v.index)
        if common_idx is None or len(common_idx) == 0:
            continue
        opens = pd.DataFrame({s: per_side[s].loc[common_idx, "implied_open"] for s in per_side})
        closes = pd.DataFrame({s: per_side[s].loc[common_idx, "implied_close"] for s in per_side})
        favorite = opens.idxmax(axis=1)
        fav_open = opens.values[np.arange(len(opens)), [list(opens.columns).index(f) for f in favorite]]
        fav_close = closes.values[np.arange(len(closes)), [list(closes.columns).index(f) for f in favorite]]
        fav_delta = fav_close - fav_open
        direction_rows.append({
            "market": market, "cut": "favorite (highest opening implied prob)",
            "n": int(len(fav_delta)),
            "pct_shortened": round(100 * float((fav_delta > 0.001).mean()), 1),
            "pct_drifted": round(100 * float((fav_delta < -0.001).mean()), 1),
            "pct_flat": round(100 * float((fav_delta.__abs__() <= 0.001).mean()), 1),
            "mean_signed_delta_pp": round(100 * float(fav_delta.mean()), 2),
        })

        if market == "ou25":
            over_oc = oc_cache.get(("ou25", "over"), pd.DataFrame())
            if not over_oc.empty:
                d = over_oc["implied_close"] - over_oc["implied_open"]
                direction_rows.append({
                    "market": "ou25", "cut": "Over side specifically (fdco-era finding check)",
                    "n": int(len(d)),
                    "pct_over_shortened": round(100 * float((d > 0.001).mean()), 1),
                    "pct_over_drifted": round(100 * float((d < -0.001).mean()), 1),
                    "mean_signed_delta_pp": round(100 * float(d.mean()), 2),
                })

    out["magnitude_by_market_bookmaker"] = magnitude_rows
    out["direction"] = direction_rows

    # ── 2.3 Timing: where does the movement happen — daily phase vs final 6h ───
    timing_rows = []
    for market in MARKETS:
        for side_name, side_col in SIDE_COLS[market]:
            d = snaps[
                (snaps["market_type"] == market)
                & (snaps["fixture_id"].isin(qualifying_ids))
                & snaps[side_col].notna()
                & (snaps["bookmaker_name"] == "Pinnacle")
            ].copy()
            if d.empty:
                continue
            d["implied"] = _implied(d[side_col])
            d = d.sort_values("captured_at")

            far = d[d["hours_to_ko"] > EARLY_HOURS]
            near = d[(d["hours_to_ko"] <= EARLY_HOURS) & (d["hours_to_ko"] >= 0)]

            open_ = far.groupby("fixture_id").first()["implied"]
            mark6h = near.groupby("fixture_id").first()["implied"]   # first near-kickoff touch, i.e. ~ the 6h mark
            close_ = near.groupby("fixture_id").last()["implied"]    # last touch before kickoff

            idx = open_.index.intersection(mark6h.index).intersection(close_.index)
            if len(idx) < 5:
                continue
            far_leg = (mark6h.loc[idx] - open_.loc[idx]).abs()
            near_leg = (close_.loc[idx] - mark6h.loc[idx]).abs()
            timing_rows.append({
                "market": market, "side": side_name, "bookmaker": "Pinnacle", "n": int(len(idx)),
                "mean_abs_move_open_to_6h_pp": round(100 * float(far_leg.mean()), 2),
                "mean_abs_move_6h_to_close_pp": round(100 * float(near_leg.mean()), 2),
                "pct_of_total_move_in_final_6h": round(
                    100 * float(near_leg.sum() / max(1e-9, (far_leg + near_leg).sum())), 1
                ),
            })
    out["timing"] = timing_rows

    # ── 2.4 Book-vs-book lead/lag ────────────────────────────────────────────
    # IMPORTANT DESIGN FACT (found while building this): one API response returns
    # every bookmaker's price for a fixture at once, so within a single scheduler
    # touch, Pinnacle and every soft book share the IDENTICAL captured_at timestamp
    # down to the microsecond (verified directly against odds_snapshots). There is
    # no continuous-time gap to measure between books -- "when Pinnacle moves, does
    # book X follow, and after how long" can only be answered in units of TOUCHES
    # (the ~1h/~24h capture cadence), not minutes. This replaces an earlier,
    # incorrect version of this analysis that computed a "delay" between Pinnacle's
    # and a soft book's nearest surrounding timestamps -- that delay was ~always
    # zero by construction and measured nothing.
    #
    # MOVE_THRESHOLD (1pp) is self-computed: touch-to-touch implied-probability
    # diffs are exactly zero ~72% of the time (no reprice that cycle) and, among
    # nonzero diffs, have a median magnitude of ~1.5pp -- 1pp sits just under that
    # median, keeping most genuine reprices while dropping the smallest, noisiest
    # residual changes.
    MOVE_THRESHOLD = 0.01

    bookvbook_rows = []
    for market in MARKETS:
        for side_name, side_col in SIDE_COLS[market]:
            d = snaps[
                (snaps["market_type"] == market)
                & (snaps["fixture_id"].isin(qualifying_ids))
                & snaps[side_col].notna()
            ].copy()
            if d.empty:
                continue
            d["implied"] = _implied(d[side_col])
            pin = d[d["bookmaker_name"] == "Pinnacle"][["fixture_id", "captured_at", "implied"]]
            pin = pin.rename(columns={"implied": "pin_implied"})

            for book in d["bookmaker_name"].unique():
                if book == "Pinnacle":
                    continue
                soft = d[d["bookmaker_name"] == book][["fixture_id", "captured_at", "implied"]]
                soft = soft.rename(columns={"implied": "soft_implied"})

                # Inner-merge on the shared touch timestamp -- both books' prices as
                # sampled at the exact same moments.
                merged = pin.merge(soft, on=["fixture_id", "captured_at"], how="inner")
                if len(merged) < 20:
                    continue
                merged = merged.sort_values(["fixture_id", "captured_at"])
                merged["pin_diff"] = merged.groupby("fixture_id")["pin_implied"].diff()
                merged["soft_diff"] = merged.groupby("fixture_id")["soft_implied"].diff()
                merged["touch_idx"] = merged.groupby("fixture_id").cumcount()

                pin_moves = merged[merged["pin_diff"].abs() >= MOVE_THRESHOLD].copy()
                if len(pin_moves) < 10:
                    continue

                same_touch = same_touch_same_dir = lag1 = lag1_same_dir = never_follows = 0
                for _, mv in pin_moves.iterrows():
                    fid, idx, pin_sign = mv["fixture_id"], mv["touch_idx"], np.sign(mv["pin_diff"])
                    soft_now = mv["soft_diff"]
                    if pd.notna(soft_now) and abs(soft_now) >= MOVE_THRESHOLD:
                        same_touch += 1
                        if np.sign(soft_now) == pin_sign:
                            same_touch_same_dir += 1
                        continue
                    # soft book didn't move at the same touch -- check the next one
                    nxt = merged[(merged["fixture_id"] == fid) & (merged["touch_idx"] == idx + 1)]
                    if not nxt.empty and pd.notna(nxt.iloc[0]["soft_diff"]) and abs(nxt.iloc[0]["soft_diff"]) >= MOVE_THRESHOLD:
                        lag1 += 1
                        if np.sign(nxt.iloc[0]["soft_diff"]) == pin_sign:
                            lag1_same_dir += 1
                    else:
                        never_follows += 1

                n = len(pin_moves)
                bookvbook_rows.append({
                    "market": market, "side": side_name, "soft_book": book,
                    "n_pinnacle_move_events": int(n),
                    "pct_soft_moves_same_touch": round(100 * same_touch / n, 1),
                    "pct_soft_moves_same_touch_same_direction": round(100 * same_touch_same_dir / n, 1),
                    "pct_soft_follows_next_touch": round(100 * lag1 / n, 1),
                    "pct_soft_follows_next_touch_same_direction": round(100 * lag1_same_dir / n, 1),
                    "pct_soft_never_follows_within_1_touch": round(100 * never_follows / n, 1),
                })
    out["book_vs_book_lead_lag"] = bookvbook_rows
    out["book_vs_book_move_threshold_pp"] = round(100 * MOVE_THRESHOLD, 1)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — Our predictions vs. the movement
# ─────────────────────────────────────────────────────────────────────────────

_PREDICTED_SIDE_COL = {
    "h2h": {"1": "odd_home", "H": "odd_home", "X": "odd_draw", "D": "odd_draw", "2": "odd_away", "A": "odd_away"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
}


def task3_track_a_vs_b(snaps: pd.DataFrame, preds: pd.DataFrame, qualifying_ids: set[int]) -> dict:
    out: dict = {}
    rows = []

    # Pinnacle carries ZERO btts odds anywhere in this system -- verified against both
    # odds_snapshots (0 of 190,923 rows) and the separate fixture_odds table (0 of 6,777
    # Pinnacle rows have odd_btts_yes populated). Not a capture gap, a market this
    # provider never lists for this bookmaker. btts falls back to 1xBet (the most
    # complete soft-book coverage, per Task 1) as its reference; h2h/ou25 use Pinnacle.
    reference_book = {"h2h": "Pinnacle", "ou25": "Pinnacle", "btts": "1xBet"}
    out["reference_bookmaker_by_market"] = reference_book

    for market in MARKETS:
        pm = preds[preds["market"] == market]
        book = reference_book[market]
        for _, prow in pm.iterrows():
            fid = prow["fixture_id"]
            if fid not in qualifying_ids:
                continue
            side_col = _PREDICTED_SIDE_COL[market].get(str(prow["predicted_outcome"]))
            if side_col is None:
                continue
            d = snaps[
                (snaps["fixture_id"] == fid) & (snaps["market_type"] == market)
                & snaps[side_col].notna() & (snaps["bookmaker_name"] == book)
            ].sort_values("captured_at")
            far = d[d["hours_to_ko"] > EARLY_HOURS]
            near = d[(d["hours_to_ko"] <= EARLY_HOURS) & (d["hours_to_ko"] >= 0)]
            if far.empty or near.empty:
                continue
            open_implied = 1.0 / far.iloc[0][side_col]
            close_implied = 1.0 / near.iloc[-1][side_col]
            our_prob = float(prow["our_prob"])

            dist_open = abs(open_implied - our_prob)
            dist_close = abs(close_implied - our_prob)
            rows.append({
                "fixture_id": int(fid), "market": market,
                "our_prob": our_prob, "open_implied": open_implied, "close_implied": close_implied,
                "dist_open": dist_open, "dist_close": dist_close,
                "toward_us": dist_close < dist_open - 1e-6,
                "away_from_us": dist_close > dist_open + 1e-6,
                "disagreement_open": abs(our_prob - open_implied),
            })

    rdf = pd.DataFrame(rows)
    out["n_matched"] = int(len(rdf))
    if rdf.empty:
        out["note"] = "No Pinnacle-covered qualifying fixtures had a matching predicted-outcome odds column."
        return out

    def _summ(d: pd.DataFrame) -> dict:
        n = len(d)
        if n == 0:
            return {"n": 0}
        return {
            "n": int(n),
            "pct_toward_us": round(100 * float(d["toward_us"].mean()), 1),
            "pct_away_from_us": round(100 * float(d["away_from_us"].mean()), 1),
            "pct_unchanged": round(100 * float((~d["toward_us"] & ~d["away_from_us"]).mean()), 1),
            "mean_dist_open_pp": round(100 * float(d["dist_open"].mean()), 2),
            "mean_dist_close_pp": round(100 * float(d["dist_close"].mean()), 2),
        }

    out["overall"] = _summ(rdf)
    out["by_market"] = {m: _summ(rdf[rdf["market"] == m]) for m in MARKETS}

    # "Model high" cut: self-computed threshold = 75th percentile of |our_prob - open_implied|
    # across the full matched sample (not a hard-coded number).
    thresh = float(rdf["disagreement_open"].quantile(0.75))
    strong = rdf[rdf["disagreement_open"] >= thresh]
    weak = rdf[rdf["disagreement_open"] < thresh]
    out["disagreement_threshold_p75_pp"] = round(100 * thresh, 2)
    out["strong_disagreement_cut"] = _summ(strong)
    out["weak_disagreement_cut"] = _summ(weak)

    return out


# ─────────────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(f"file:{DB_MAIN}?mode=ro", uri=True)
    snaps = load_snapshots(conn)
    preds = load_predictions(conn)

    t1 = task1_data_quality(snaps)
    qualifying_ids = set(t1.pop("_qualifying_fixture_ids"))

    t2 = task2_movement(snaps, qualifying_ids)
    t3 = task3_track_a_vs_b(snaps, preds, qualifying_ids)

    results = {
        "generated_from_rows": int(len(snaps)),
        "task1_data_quality": t1,
        "task2_movement": t2,
        "task3_our_predictions": t3,
    }
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"Wrote {OUT_JSON}")
    print(json.dumps(results, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
