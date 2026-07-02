#!/usr/bin/env python3
"""
scripts/probe_forward_odds.py — READ-ONLY VERIFICATION CHECKPOINT (Phase 25)

Originally a live probe (Phase 12b) that fetched its own odds to detect whether
Pinnacle posts for Tasmania NPL / Norwegian 3.Division before kickoff. Superseded
by scripts/odds_trajectory_scheduler.py, which now covers ALL odds-carrying
fixtures — including these leagues — on its own daily→hourly schedule. A second
independent fetcher hitting the same fixtures would double-spend API calls and
could race the scheduler on odds_snapshots writes (Phase 25 Task 3).

This script no longer calls the API. It reads what the scheduler has already
captured for the given leagues and reports whether coverage looks as expected:
fixtures found, snapshots per fixture, and — the original point of the experiment
— whether Pinnacle shows up, specifically in the NEAR-KICKOFF window (Phase 11b:
Pinnacle often posts only close to kickoff, so absence in early snapshots alone
proves nothing).

Same CLI and cron entry point as before (--league-ids, --days-ahead) so the
existing crontab lines (Tasmania July 3-4, Norway July 24) don't need to change —
only what runs under them does.

Usage:
    python scripts/probe_forward_odds.py --league-ids 648
    python scripts/probe_forward_odds.py --league-ids 777,778,779 --days-ahead 2
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.storage.db import get_session, init_db
from src.storage.models import Fixture, OddsSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("probe_forward_odds")

PINNACLE_NAME = "Pinnacle"
NEAR_KICKOFF_HOURS = 6.0
FLAG_FILE = Path("logs/soft_book_decision_needed.txt")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_target_fixtures(league_ids: list[int], days_ahead: int) -> list[dict]:
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


def _snapshot_summary(fixture_id: int, kickoff: datetime) -> dict:
    with get_session() as s:
        rows = s.execute(
            select(OddsSnapshot.bookmaker_name, OddsSnapshot.captured_at)
            .where(OddsSnapshot.fixture_id == fixture_id)
        ).fetchall()

    near_cutoff = kickoff - timedelta(hours=NEAR_KICKOFF_HOURS)
    all_books = {name for name, _ in rows}
    near_books = {name for name, captured_at in rows if captured_at >= near_cutoff}

    return {
        "total_snapshots": len(rows),
        "distinct_bookmakers": sorted(all_books),
        "pinnacle_ever": PINNACLE_NAME in all_books,
        "pinnacle_near_kickoff": PINNACLE_NAME in near_books,
        "near_kickoff_snapshots": len(near_books) if near_books else 0,
    }


def run(league_ids: list[int], days_ahead: int = 2) -> dict:
    init_db()
    now = _utcnow()

    stats = {
        "league_ids": league_ids,
        "fixtures_found": 0,
        "fixtures_with_any_snapshot": 0,
        "fixtures_with_pinnacle_ever": 0,
        "fixtures_with_pinnacle_near_kickoff": 0,
        "fixtures_no_snapshot_yet": [],
        "fixtures_imminent_soft_only": [],  # <6h to kickoff, has soft books, no Pinnacle
        "per_fixture": {},
    }

    fixtures = _get_target_fixtures(league_ids, days_ahead)
    stats["fixtures_found"] = len(fixtures)

    if not fixtures:
        logger.info(
            "CHECKPOINT: No NS fixtures in leagues %s within %d days. "
            "If leagues are on break, this is expected.",
            league_ids, days_ahead,
        )
        return stats

    for fix in fixtures:
        summary = _snapshot_summary(fix["id"], fix["date"])
        stats["per_fixture"][fix["id"]] = summary

        if summary["total_snapshots"] > 0:
            stats["fixtures_with_any_snapshot"] += 1
        else:
            stats["fixtures_no_snapshot_yet"].append(fix["id"])

        if summary["pinnacle_ever"]:
            stats["fixtures_with_pinnacle_ever"] += 1
        if summary["pinnacle_near_kickoff"]:
            stats["fixtures_with_pinnacle_near_kickoff"] += 1

        hours_to_ko = (fix["date"] - now).total_seconds() / 3600.0
        if (
            0 <= hours_to_ko <= NEAR_KICKOFF_HOURS
            and summary["distinct_bookmakers"]
            and not summary["pinnacle_near_kickoff"]
        ):
            stats["fixtures_imminent_soft_only"].append(fix["id"])

        logger.info(
            "CHECKPOINT: fixture=%d date=%s snapshots=%d bookmakers=%s pinnacle_ever=%s pinnacle_near_ko=%s",
            fix["id"], fix["date"], summary["total_snapshots"], summary["distinct_bookmakers"],
            summary["pinnacle_ever"], summary["pinnacle_near_kickoff"],
        )

    if stats["fixtures_imminent_soft_only"]:
        _write_soft_book_flag(league_ids, stats)

    return stats


def _write_soft_book_flag(league_ids: list[int], stats: dict) -> None:
    """Same decision-needed flag as before, now sourced from the scheduler's own captures
    instead of a live probe fetch."""
    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    now_str = _utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with open(FLAG_FILE, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"CHECKPOINT RUN: {now_str}\n")
        f.write(f"Leagues: {league_ids}\n")
        f.write(f"Fixtures imminent (<{NEAR_KICKOFF_HOURS}h) with soft books but no Pinnacle: "
                f"{stats['fixtures_imminent_soft_only']}\n")
        for fid in stats["fixtures_imminent_soft_only"]:
            f.write(f"  fixture {fid}: bookmakers={stats['per_fixture'][fid]['distinct_bookmakers']}\n")
        f.write(
            "\nUSER DECISION REQUIRED:\n"
            "  These fixtures are within the near-kickoff window and the scheduler has\n"
            "  captured soft-book odds but no Pinnacle. Options:\n"
            "  A) Run Track B on Bet365 as reference (soft-book correlation risk)\n"
            "  B) Shelve these fixtures as Pinnacle-absent (no CLV measurement)\n"
            "  C) Wait — the scheduler is still capturing hourly; re-check closer to kickoff\n"
        )
    logger.warning(
        "CHECKPOINT: SOFT-BOOK FLAG written to %s — %d fixture(s) imminent with no Pinnacle yet.",
        FLAG_FILE, len(stats["fixtures_imminent_soft_only"]),
    )


def main():
    parser = argparse.ArgumentParser(description="Read-only verification checkpoint for forward leagues")
    parser.add_argument(
        "--league-ids",
        required=True,
        help="Comma-separated league IDs to check (e.g. 648 or 777,778,779)",
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

    print("\n── Forward Collection Checkpoint (read-only) ───────────")
    print(f"  Date:                        {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Leagues:                     {league_ids}")
    print(f"  Fixtures found:              {stats['fixtures_found']}")
    print(f"  Fixtures with any snapshot:  {stats['fixtures_with_any_snapshot']}")
    print(f"  Fixtures with Pinnacle ever: {stats['fixtures_with_pinnacle_ever']}")
    print(f"  Fixtures with Pinnacle near kickoff: {stats['fixtures_with_pinnacle_near_kickoff']}")
    if stats["fixtures_no_snapshot_yet"]:
        print(f"  No snapshot yet (scheduler hasn't reached them): {stats['fixtures_no_snapshot_yet']}")
    if stats["fixtures_imminent_soft_only"]:
        print(f"\n  *** USER DECISION REQUIRED — see {FLAG_FILE} ***")
        print(f"  Imminent, soft-only: {stats['fixtures_imminent_soft_only']}")
    print("─────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
