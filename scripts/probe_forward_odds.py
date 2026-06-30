#!/usr/bin/env python3
"""
scripts/probe_forward_odds.py

Bookmaker-detection probe for forward leagues before first odds snapshot.

Unlike capture_forward_odds.py (which silently skips non-Pinnacle bookmakers),
this script logs the RAW bookmaker list and raw bet_name strings for every API
response, then decides what to write based on what was actually found:

  - Pinnacle present → write to odds_snapshots (clock starts)
  - Only soft books → write flag file for user decision (do NOT write to DB)
  - No odds at all   → log as Pinnacle-absent candidate

This is a one-shot probe, not a continuous collector.  Schedule via cron for
24-48h before target kickoffs.

Usage:
    python scripts/probe_forward_odds.py --league-ids 648
    python scripts/probe_forward_odds.py --league-ids 777,778,779 --days-ahead 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from config.forward_leagues import CAPTURE_BOOKMAKERS, CAPTURE_MARKETS
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, OddsSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("probe_forward_odds")

PINNACLE_ID = 4
PINNACLE_NAME = "Pinnacle"
FLAG_FILE = Path("logs/soft_book_decision_needed.txt")
LOG_DIR = Path("logs")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_upcoming_fixtures(league_ids: list[int], days_ahead: int) -> list[dict]:
    """Return NS fixtures in the given leagues within days_ahead."""
    cutoff = _utcnow() + timedelta(days=days_ahead)
    now = _utcnow()
    with get_session() as s:
        rows = s.execute(
            select(Fixture.id, Fixture.date, Fixture.league_id)
            .where(Fixture.league_id.in_(league_ids))
            .where(Fixture.date >= now - timedelta(hours=2))
            .where(Fixture.date <= cutoff)
            .where(Fixture.status.in_(["NS", "1H", "HT", "2H"]))
        ).fetchall()
    return [{"id": r[0], "date": r[1], "league_id": r[2]} for r in rows]


def _write_odds_snapshot(
    fixture_id: int,
    bm_id: int,
    bm_name: str,
    market_type: str,
    odds_kwargs: dict,
    now: datetime,
    s,
) -> None:
    row = OddsSnapshot(
        fixture_id=fixture_id,
        bookmaker_id=bm_id,
        bookmaker_name=bm_name,
        market_type=market_type,
        captured_at=now,
        **odds_kwargs,
    )
    s.add(row)
    s.flush()


def _parse_h2h(values: list[dict]) -> dict:
    out: dict = {}
    for v in values:
        hand = str(v.get("value", ""))
        try:
            f = float(v["odd"])
        except (KeyError, ValueError, TypeError):
            continue
        if hand == "Home":
            out["odd_home"] = f
        elif hand == "Draw":
            out["odd_draw"] = f
        elif hand == "Away":
            out["odd_away"] = f
    return out


def _parse_ou25(values: list[dict]) -> dict:
    out: dict = {}
    for v in values:
        hand = str(v.get("value", ""))
        try:
            f = float(v["odd"])
        except (KeyError, ValueError, TypeError):
            continue
        if hand == "Over 2.5":
            out["odd_over"] = f
        elif hand == "Under 2.5":
            out["odd_under"] = f
    return out


def _parse_btts(values: list[dict]) -> dict:
    out: dict = {}
    for v in values:
        hand = str(v.get("value", ""))
        try:
            f = float(v["odd"])
        except (KeyError, ValueError, TypeError):
            continue
        if hand == "Yes":
            out["odd_btts_yes"] = f
        elif hand == "No":
            out["odd_btts_no"] = f
    return out


def run(league_ids: list[int], days_ahead: int = 2) -> dict:
    init_db()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    client = APIFootballClient()
    now = _utcnow()

    stats = {
        "league_ids": league_ids,
        "fixtures_found": 0,
        "pinnacle_present": [],
        "soft_only": [],
        "no_odds": [],
        "api_calls": 0,
        "rows_written": 0,
        "raw_bookmakers_seen": {},
        "raw_bet_names_seen": {},
    }

    fixtures = _get_upcoming_fixtures(league_ids, days_ahead)
    stats["fixtures_found"] = len(fixtures)

    if not fixtures:
        logger.info(
            "PROBE: No NS fixtures found in leagues %s within %d days. "
            "If leagues are on break, this is expected.",
            league_ids, days_ahead
        )
        return stats

    logger.info(
        "PROBE: Found %d fixtures in leagues %s. Fetching raw odds (no bookmaker filter).",
        len(fixtures), league_ids
    )

    with get_session() as s:
        for fix in fixtures:
            fixture_id = fix["id"]
            fixture_date = fix["date"]
            all_bookmakers_for_fixture: set[str] = set()
            fixture_rows_written = 0

            for market_name, bet_type_id in CAPTURE_MARKETS.items():
                try:
                    raw = client.get_odds(fixture_id=fixture_id, bet_type=bet_type_id)
                    stats["api_calls"] += 1
                except Exception as exc:
                    logger.warning("PROBE: API error fixture=%d market=%s: %s", fixture_id, market_name, exc)
                    continue

                if not raw:
                    logger.debug("PROBE: No odds for fixture=%d market=%s", fixture_id, market_name)
                    continue

                for fixture_block in raw:
                    for bm in fixture_block.get("bookmakers", []):
                        bm_name = bm.get("name", "Unknown")
                        all_bookmakers_for_fixture.add(bm_name)

                        # Log raw bet_name strings for every bookmaker (Phase 11b unverified names)
                        for bet in bm.get("bets", []):
                            bet_name = bet.get("name", "")
                            key = f"{bm_name}:{bet_name}"
                            stats["raw_bet_names_seen"][key] = stats["raw_bet_names_seen"].get(key, 0) + 1

                        # Only write to DB for Pinnacle
                        if bm_name != PINNACLE_NAME:
                            continue

                        bm_id = PINNACLE_ID
                        for bet in bm.get("bets", []):
                            bet_name = bet.get("name", "")
                            values = bet.get("values", [])
                            if not values:
                                continue

                            if bet_name in ("Match Winner", "1x2"):
                                parsed = _parse_h2h(values)
                                if parsed:
                                    _write_odds_snapshot(fixture_id, bm_id, bm_name, "h2h", parsed, now, s)
                                    fixture_rows_written += 1

                            elif bet_name in ("Goals Over/Under", "Over/Under"):
                                parsed = _parse_ou25(values)
                                if parsed:
                                    _write_odds_snapshot(fixture_id, bm_id, bm_name, "ou25", parsed, now, s)
                                    fixture_rows_written += 1

                            elif bet_name == "Both Teams Score":
                                parsed = _parse_btts(values)
                                if parsed:
                                    _write_odds_snapshot(fixture_id, bm_id, bm_name, "btts", parsed, now, s)
                                    fixture_rows_written += 1

                            else:
                                # Unrecognised bet_name — log it prominently
                                logger.warning(
                                    "PROBE: Pinnacle returned unrecognised bet_name=%r for "
                                    "fixture=%d market=%s — NOT written. Add to parser if needed.",
                                    bet_name, fixture_id, market_name
                                )

            # Record bookmakers seen for this fixture
            stats["raw_bookmakers_seen"][str(fixture_id)] = sorted(all_bookmakers_for_fixture)
            stats["rows_written"] += fixture_rows_written

            # Classify this fixture
            if PINNACLE_NAME in all_bookmakers_for_fixture:
                stats["pinnacle_present"].append(fixture_id)
                logger.info(
                    "PROBE: fixture=%d date=%s — PINNACLE PRESENT. %d rows written. "
                    "All bookmakers: %s",
                    fixture_id, fixture_date, fixture_rows_written,
                    sorted(all_bookmakers_for_fixture)
                )
            elif all_bookmakers_for_fixture:
                stats["soft_only"].append(fixture_id)
                logger.warning(
                    "PROBE: fixture=%d date=%s — SOFT BOOKS ONLY (Pinnacle absent). "
                    "Bookmakers: %s. NOT written to DB — user decision required.",
                    fixture_id, fixture_date, sorted(all_bookmakers_for_fixture)
                )
            else:
                stats["no_odds"].append(fixture_id)
                logger.warning(
                    "PROBE: fixture=%d date=%s — NO ODDS RETURNED (Pinnacle-absent candidate).",
                    fixture_id, fixture_date
                )

        if not stats["pinnacle_present"] and stats["rows_written"] == 0:
            pass  # no commit needed
        else:
            s.commit()

    # Write soft-book decision flag if needed
    if stats["soft_only"] and not stats["pinnacle_present"]:
        _write_soft_book_flag(league_ids, stats)
    elif stats["soft_only"]:
        logger.info(
            "PROBE: %d fixtures had Pinnacle, %d had soft-only. "
            "Pinnacle rows written for Pinnacle fixtures only.",
            len(stats["pinnacle_present"]), len(stats["soft_only"])
        )

    return stats


def _write_soft_book_flag(league_ids: list[int], stats: dict) -> None:
    """Write flag file signalling that user decision is needed on soft-book reference."""
    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    now_str = _utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with open(FLAG_FILE, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"PROBE RUN: {now_str}\n")
        f.write(f"Leagues: {league_ids}\n")
        f.write(f"Fixtures soft-only (no Pinnacle): {stats['soft_only']}\n")
        f.write(f"Fixtures no odds at all: {stats['no_odds']}\n")
        f.write(f"Bookmakers found per fixture:\n")
        for fix_id, bms in stats["raw_bookmakers_seen"].items():
            f.write(f"  fixture {fix_id}: {bms}\n")
        f.write(
            "\nUSER DECISION REQUIRED:\n"
            "  Pinnacle is absent for these leagues. Options:\n"
            "  A) Run Track B on Bet365 as reference (soft-book correlation risk)\n"
            "  B) Shelve leagues as Pinnacle-absent (no CLV measurement)\n"
            "  C) Wait longer before kickoff and re-probe\n"
            "  Do NOT modify capture_forward_odds.py or start logging soft-book odds\n"
            "  until this decision is made.\n"
        )
    logger.warning(
        "PROBE: SOFT-BOOK FLAG written to %s — USER DECISION REQUIRED before any "
        "soft-book odds are logged to odds_snapshots.",
        FLAG_FILE
    )


def main():
    parser = argparse.ArgumentParser(description="Bookmaker-detection probe for forward leagues")
    parser.add_argument(
        "--league-ids",
        required=True,
        help="Comma-separated league IDs to probe (e.g. 648 or 777,778,779)",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=2,
        help="Days ahead to scan for NS fixtures (default 2)",
    )
    args = parser.parse_args()

    league_ids = [int(x.strip()) for x in args.league_ids.split(",")]
    stats = run(league_ids=league_ids, days_ahead=args.days_ahead)

    print("\n── Forward Probe Run ────────────────────────────────────")
    print(f"  Date:              {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Leagues:           {league_ids}")
    print(f"  Fixtures found:    {stats['fixtures_found']}")
    print(f"  API calls used:    {stats['api_calls']}")
    print(f"  Rows written:      {stats['rows_written']}")
    print(f"  Pinnacle present:  {stats['pinnacle_present']}")
    print(f"  Soft-only:         {stats['soft_only']}")
    print(f"  No odds:           {stats['no_odds']}")
    if stats["raw_bookmakers_seen"]:
        print("  Bookmakers per fixture:")
        for fix_id, bms in stats["raw_bookmakers_seen"].items():
            print(f"    {fix_id}: {bms}")
    if stats["raw_bet_names_seen"]:
        print("  Raw bet_name strings seen:")
        for name, count in sorted(stats["raw_bet_names_seen"].items()):
            print(f"    {name}: {count}")
    if stats["soft_only"] and not stats["pinnacle_present"]:
        print(f"\n  *** USER DECISION REQUIRED — see {FLAG_FILE} ***")
    print("────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
