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
import os
import sys
from pathlib import Path

# Ensure we always run from the project root so .env and relative paths resolve correctly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from src.ingestion.client import get_api_status


def _remaining() -> int:
    return get_api_status()['remaining']
from config.leagues import ALL_LEAGUE_IDS
from src.storage.db import get_session
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

STOP_AT_REMAINING = 25_000
# Historical seasons first — 2026 is the live season, most leagues have no FT
# data yet so checking them first wastes ~938 calls/day returning empty.
TARGET_SEASONS = [2025, 2024, 2023, 2022, 2021, 2026]


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

    remaining = _remaining()
    logger.info(f"API calls remaining at start: {remaining}")
    logger.info(f"Total leagues to cover: {len(ALL_LEAGUE_IDS)}")

    if remaining < STOP_AT_REMAINING + 5_000:
        logger.info(f"Fewer than {STOP_AT_REMAINING + 5_000} calls available — skipping today's run")
        _log_run(skipped=True, summary=f"skipped: only {remaining} calls remaining")
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
        _log_run(skipped=False, summary="all seasons already covered")
        return

    from scripts.backfill_all import EuropeanBackfiller

    backfiller = EuropeanBackfiller()

    for season, missing_leagues in seasons_to_run:
        remaining = _remaining()
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

        remaining = _remaining()
        logger.info(f"After season {season}: {remaining} calls remaining")

        if remaining < STOP_AT_REMAINING:
            logger.info("Budget threshold reached — stopping after this season")
            break

    calls_after_fixtures = _remaining()
    logger.info(f"After fixture pass: {calls_after_fixtures} calls remaining")

    # Phase 2: fill in missing stats for already-covered fixtures.
    if calls_after_fixtures > STOP_AT_REMAINING:
        stats_fetched = _backfill_missing_stats(stop_at=STOP_AT_REMAINING)
        logger.info(f"Stats pass fetched: {stats_fetched}")

    # Phase 3: per-season player statistics (~67k calls across 5 seasons, resumable).
    calls_after_stats = _remaining()
    if calls_after_stats > STOP_AT_REMAINING:
        from scripts.backfill_player_stats import run_backfill as _run_player_backfill
        player_rows = _run_player_backfill(stop_at=STOP_AT_REMAINING)
        logger.info(f"Player stats pass inserted: {player_rows} rows")

    calls_end = _remaining()
    logger.info(f"Cron run complete. API calls remaining: {calls_end}")

    _log_run(skipped=False, summary=f"calls_remaining={calls_end}")


def _backfill_missing_stats(stop_at: int) -> int:
    """Fetch fixture statistics for FT fixtures that have none yet.

    Works through seasons oldest-to-newest (2021 → 2025) to complete
    the historical record first. Stops when remaining quota drops to stop_at.
    Returns count of stats records inserted.
    """
    from scripts.backfill_all import EuropeanBackfiller
    from src.storage.models import Fixture, FixtureStats
    from sqlalchemy import select as _select

    fetched = 0
    client_wrapper = EuropeanBackfiller()

    for season in [2021, 2022, 2023, 2024, 2025, 2026]:
        if _remaining() <= stop_at:
            break

        with get_session() as s:
            # Fixtures in this season with no stats row
            have_stats = {r[0] for r in s.execute(
                text(f"SELECT DISTINCT fixture_id FROM fixture_stats "
                     f"WHERE fixture_id IN (SELECT id FROM fixtures WHERE season={season} AND status='FT')")
            ).fetchall()}
            all_ft = [r[0] for r in s.execute(
                text(f"SELECT id FROM fixtures WHERE season={season} AND status='FT'")
            ).fetchall()]

        missing = [fid for fid in all_ft if fid not in have_stats]
        if not missing:
            logger.info(f"Stats: season {season} fully covered ({len(all_ft)} fixtures)")
            continue

        logger.info(f"Stats: season {season} — {len(missing)} fixtures need stats")
        for fid in missing:
            if _remaining() <= stop_at:
                logger.info(f"Stats pass: quota threshold reached, stopping")
                return fetched
            try:
                raw = client_wrapper.client.get_fixture_statistics(fid)
                if raw:
                    client_wrapper._parse_and_store_stats(fid, raw)
                    fetched += 1
            except Exception as exc:
                logger.warning(f"Stats fetch failed for fixture {fid}: {exc}")

        logger.info(f"Stats: season {season} done this pass, fetched={fetched}")

    return fetched


def _log_run(skipped: bool, summary: str) -> None:
    try:
        with get_session() as s:
            s.execute(text("""
                INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message)
                VALUES (:job, 1, 0, :summary)
            """), {"job": "backfill_cron", "summary": summary})
            s.commit()
    except Exception as exc:
        logger.warning(f"Failed to log run: {exc}")


if __name__ == '__main__':
    main()
