#!/usr/bin/env python3
"""
scripts/backfill_cron.py - Daily 4am backfill job.

Automatically picks the next uncovered season for ALL_LEAGUE_IDS (1225+) and
backfills until ~15 000 API calls remain for the day.

Seasons are processed newest-to-oldest (2026 → 2025 → 2024 → ...).
Stops when either:
  - All target seasons are fully covered, or
  - API calls remaining drops below STOP_AT_REMAINING
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.client import calls_remaining_today
from config.leagues import ALL_LEAGUE_IDS
from src.storage.db import get_session
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

STOP_AT_REMAINING = 15_000
# Seasons to work through, newest-first so recent history fills in first.
TARGET_SEASONS = [2026, 2025, 2024, 2023, 2022, 2021]


def _covered_pairs() -> set:
    """Return set of (league_id, season) pairs that already have FT fixture data."""
    with get_session() as s:
        rows = s.execute(text("""
            SELECT DISTINCT league_id, season
            FROM fixtures
            WHERE status = 'FT'
        """)).fetchall()
    return {(r[0], r[1]) for r in rows}


def main():
    from src.storage.db import init_db
    init_db()

    remaining = calls_remaining_today()
    logger.info(f"API calls remaining at start: {remaining}")
    logger.info(f"Total leagues to cover: {len(ALL_LEAGUE_IDS)}")

    if remaining < STOP_AT_REMAINING + 5_000:
        logger.info(f"Fewer than {STOP_AT_REMAINING + 5_000} calls available — skipping today's run")
        return

    covered = _covered_pairs()
    logger.info(f"Already covered: {len(covered)} (league, season) pairs")

    # Find which seasons still have missing leagues
    seasons_to_run = []
    for season in TARGET_SEASONS:
        missing = [lid for lid in ALL_LEAGUE_IDS if (lid, season) not in covered]
        if missing:
            seasons_to_run.append((season, missing))
            logger.info(f"Season {season}: {len(missing)}/{len(ALL_LEAGUE_IDS)} leagues still missing")
        else:
            logger.info(f"Season {season}: fully covered, skipping")

    if not seasons_to_run:
        logger.info("All target seasons fully covered — nothing to do")
        return

    from scripts.backfill_all import EuropeanBackfiller

    backfiller = EuropeanBackfiller()

    for season, missing_leagues in seasons_to_run:
        remaining = calls_remaining_today()
        if remaining < STOP_AT_REMAINING:
            logger.info(f"Budget threshold reached ({remaining} remaining) — stopping")
            break

        logger.info(f"=== Backfilling season {season} ({len(missing_leagues)} leagues) ===")
        backfiller.run(
            seasons=[season],
            include_odds=False,
            leagues=missing_leagues,
            stop_at_remaining=STOP_AT_REMAINING,
        )

        remaining = calls_remaining_today()
        logger.info(f"After season {season}: {remaining} calls remaining")

        if remaining < STOP_AT_REMAINING:
            logger.info("Budget threshold reached — stopping after this season")
            break

    logger.info(f"Cron run complete. API calls remaining: {calls_remaining_today()}")


if __name__ == '__main__':
    main()
