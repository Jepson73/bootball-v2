#!/usr/bin/env python3
"""
scripts/capture_forward_odds.py

SUPERSEDED (Phase 25) — do not add to cron or run alongside
scripts/odds_trajectory_scheduler.py, which now covers ALL odds-carrying
fixtures (including these 4-5 forward leagues) on a daily→hourly schedule and
writes the same odds_snapshots table. Running this script too would double-spend
API calls and race the scheduler on writes. It was never wired into
/etc/cron.d/bootball (verified Phase 25) — this docstring exists to keep it that
way. scripts/probe_forward_odds.py now serves the original Tasmania/Norway
experiment as a read-only checkpoint against what the scheduler already captured.

Left in place, not deleted, in case its narrow Pinnacle+Bet365-only capture logic
is ever useful as a reference. Original docstring follows.

---

Forward-collection odds capture for long-tail Pinnacle-covered leagues.

Run multiple times per day (e.g. every 4 hours via cron) to build an
open→close odds time-series in the odds_snapshots table.

Usage:
    python scripts/capture_forward_odds.py [--dry-run] [--days-ahead N]

    --dry-run      : fetch and parse odds but do not write to DB
    --days-ahead N : how far ahead to look for upcoming fixtures (default 7)

What it does:
  1. Query DB for upcoming fixtures in FORWARD_LEAGUES (within days_ahead).
  2. For each fixture not yet captured within CAPTURE_STALE_HOURS, call the
     API-Football /odds endpoint for each CAPTURE_MARKETS bet type.
  3. Filter response to CAPTURE_BOOKMAKERS; store one OddsSnapshot row per
     (fixture, bookmaker, market, now) — preserving all captures over time.
  4. Print a summary: fixtures found, captured, API calls used, rows written.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func, text

from config.forward_leagues import (
    FORWARD_LEAGUES,
    FORWARD_LEAGUE_IDS,
    CAPTURE_BOOKMAKERS,
    CAPTURE_MARKETS,
    CAPTURE_STALE_HOURS,
    CURRENT_SEASON,
)
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, OddsSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("capture_forward_odds")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_over25(bet_values: list[dict]) -> tuple[float | None, float | None]:
    """Extract 2.5 over/under from an Over_Under bet's values list."""
    over: float | None = None
    under: float | None = None
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Over 2.5":
            over = f
        elif hand == "Under 2.5":
            under = f
    return over, under


def _parse_h2h(bet_values: list[dict]) -> tuple[float | None, float | None, float | None]:
    home = draw = away = None
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Home":
            home = f
        elif hand == "Draw":
            draw = f
        elif hand == "Away":
            away = f
    return home, draw, away


def _parse_btts(bet_values: list[dict]) -> tuple[float | None, float | None]:
    yes = no = None
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Yes":
            yes = f
        elif hand == "No":
            no = f
    return yes, no


def _get_upcoming_fixture_ids(s, days_ahead: int) -> list[int]:
    """Return IDs of NS/1H fixtures in FORWARD_LEAGUES within days_ahead days."""
    cutoff = _utcnow() + timedelta(days=days_ahead)
    now = _utcnow()

    rows = s.execute(
        select(Fixture.id)
        .where(Fixture.league_id.in_(FORWARD_LEAGUE_IDS))
        .where(Fixture.date >= now - timedelta(hours=2))  # allow in-progress
        .where(Fixture.date <= cutoff)
        .where(Fixture.status.in_(["NS", "1H", "HT", "2H", "ET", "BT", "P"]))
    ).fetchall()
    return [r[0] for r in rows]


def _already_captured_within_stale(s, fixture_id: int, market_type: str, bookmaker_name: str) -> bool:
    """True if a snapshot for this combo exists within CAPTURE_STALE_HOURS."""
    threshold = _utcnow() - timedelta(hours=CAPTURE_STALE_HOURS)
    count = s.execute(
        select(func.count())
        .select_from(OddsSnapshot)
        .where(OddsSnapshot.fixture_id == fixture_id)
        .where(OddsSnapshot.market_type == market_type)
        .where(OddsSnapshot.bookmaker_name == bookmaker_name)
        .where(OddsSnapshot.captured_at >= threshold)
    ).scalar()
    return (count or 0) > 0


def _process_odds_response(
    raw_odds: list[dict],
    fixture_id: int,
    now: datetime,
    dry_run: bool,
    s,
) -> int:
    """Parse API-Football /odds response and insert OddsSnapshot rows. Returns rows written."""
    written = 0

    for fixture_block in raw_odds:
        bookmakers = fixture_block.get("bookmakers", [])
        for bm in bookmakers:
            bm_name = bm.get("name", "Unknown")
            # Find bookmaker_id by matching name
            bm_id = next(
                (bid for bid, bname in CAPTURE_BOOKMAKERS.items() if bname == bm_name),
                None,
            )
            if bm_name not in CAPTURE_BOOKMAKERS.values():
                continue  # skip bookmakers not in our capture list

            for bet in bm.get("bets", []):
                bet_name = bet.get("name", "")
                bet_values = bet.get("values", [])
                if not bet_values:
                    continue

                snapshot_kwargs: dict = {
                    "fixture_id": fixture_id,
                    "bookmaker_id": bm_id,
                    "bookmaker_name": bm_name,
                    "captured_at": now,
                }

                # NOTE: bet_name strings ("Match Winner", "Goals Over/Under", "Both Teams Score")
                # were inferred from API-Football docs and prior FixtureOdds data. Verify on
                # first live run: SELECT DISTINCT bet_name FROM raw API response logs.
                # If Pinnacle uses a different name (e.g. "1x2"), add it to the tuple.
                if bet_name in ("Match Winner", "1x2"):
                    if _already_captured_within_stale(s, fixture_id, "h2h", bm_name):
                        continue
                    home, draw, away = _parse_h2h(bet_values)
                    if home is None and draw is None and away is None:
                        continue
                    row = OddsSnapshot(
                        **snapshot_kwargs,
                        market_type="h2h",
                        odd_home=home,
                        odd_draw=draw,
                        odd_away=away,
                    )
                elif bet_name in ("Goals Over/Under", "Over/Under"):
                    if _already_captured_within_stale(s, fixture_id, "ou25", bm_name):
                        continue
                    over, under = _parse_over25(bet_values)
                    if over is None and under is None:
                        continue
                    row = OddsSnapshot(
                        **snapshot_kwargs,
                        market_type="ou25",
                        odd_over=over,
                        odd_under=under,
                    )
                elif bet_name == "Both Teams Score":
                    if _already_captured_within_stale(s, fixture_id, "btts", bm_name):
                        continue
                    yes, no = _parse_btts(bet_values)
                    if yes is None and no is None:
                        continue
                    row = OddsSnapshot(
                        **snapshot_kwargs,
                        market_type="btts",
                        odd_btts_yes=yes,
                        odd_btts_no=no,
                    )
                else:
                    logger.debug("Unrecognised bet_name=%r for fixture=%d bm=%r — skipping", bet_name, fixture_id, bm_name)
                    continue

                if not dry_run:
                    s.add(row)
                    s.flush()
                written += 1

    return written


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, days_ahead: int = 7) -> dict:
    init_db()
    client = APIFootballClient()
    now = _utcnow()

    stats = {
        "fixtures_found": 0,
        "fixtures_skipped_stale": 0,
        "fixtures_attempted": 0,
        "api_calls": 0,
        "rows_written": 0,
        "errors": [],
    }

    with get_session() as s:
        fixture_ids = _get_upcoming_fixture_ids(s, days_ahead)
        stats["fixtures_found"] = len(fixture_ids)

        if not fixture_ids:
            logger.info("No upcoming fixtures in forward leagues within %d days.", days_ahead)
            return stats

        logger.info(
            "Found %d upcoming fixtures in forward leagues (%s).",
            len(fixture_ids),
            ", ".join(str(lid) for lid in FORWARD_LEAGUE_IDS),
        )

        for fixture_id in fixture_ids:
            fixture_had_any_new = False

            for market_name, bet_id in CAPTURE_MARKETS.items():
                # Quick stale check across all capture bookmakers for this market
                all_stale = all(
                    _already_captured_within_stale(s, fixture_id, _api_market_to_type(market_name), bname)
                    for bname in CAPTURE_BOOKMAKERS.values()
                )
                if all_stale:
                    continue

                fixture_had_any_new = True
                try:
                    raw = client.get_odds(fixture_id=fixture_id, bet_type=bet_id)
                    stats["api_calls"] += 1
                except Exception as exc:
                    logger.warning("API error for fixture %d market %s: %s", fixture_id, market_name, exc)
                    stats["errors"].append(f"fixture={fixture_id} market={market_name}: {exc}")
                    continue

                if not raw:
                    logger.debug("No odds returned for fixture %d market %s", fixture_id, market_name)
                    continue

                written = _process_odds_response(raw, fixture_id, now, dry_run, s)
                stats["rows_written"] += written

                if written:
                    logger.info(
                        "fixture=%d market=%s: wrote %d row(s)%s",
                        fixture_id,
                        market_name,
                        written,
                        " [DRY RUN]" if dry_run else "",
                    )

            if not fixture_had_any_new:
                stats["fixtures_skipped_stale"] += 1
            else:
                stats["fixtures_attempted"] += 1

        if not dry_run:
            s.commit()

    return stats


def _api_market_to_type(market_name: str) -> str:
    return {"h2h": "h2h", "over_under": "ou25", "btts": "btts"}.get(market_name, market_name)


def main():
    parser = argparse.ArgumentParser(description="Capture forward-collection odds time-series")
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write to DB")
    parser.add_argument("--days-ahead", type=int, default=7, help="Days ahead to scan (default 7)")
    args = parser.parse_args()

    stats = run(dry_run=args.dry_run, days_ahead=args.days_ahead)

    print("\n── Forward Collection Run ──────────────────────────────")
    print(f"  Date:               {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Leagues:            {FORWARD_LEAGUE_IDS}")
    print(f"  Fixtures found:     {stats['fixtures_found']}")
    print(f"  Fixtures attempted: {stats['fixtures_attempted']}")
    print(f"  Fixtures skipped (stale): {stats['fixtures_skipped_stale']}")
    print(f"  API calls:          {stats['api_calls']}")
    print(f"  Rows written:       {stats['rows_written']}  {'[DRY RUN]' if args.dry_run else ''}")
    if stats["errors"]:
        print(f"  Errors ({len(stats['errors'])}):")
        for e in stats["errors"]:
            print(f"    {e}")
    print("────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
