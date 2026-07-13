"""
scripts/analysis/phase37_backfill_injuries.py

Phase 37 Part A.3 — historical injuries backfill for settled covered-league
fixtures (Part B's actual training fuel). One call per fixture via
GET /injuries?fixture=X (no batch endpoint exists for this).

Fixes the field-mapping bug in scripts/fetch_player_data.py (dead code): reads
player.reason (free-text detail) instead of team.reason (doesn't exist).
Stores fetched_at (today, bookkeeping only -- see migration 032's docstring
for why this is NOT the leakage boundary for this historical-backfill mode).

Projected cost stated before running: len(fixtures without an injuries row)
calls, 1 call per fixture.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from config.covered_leagues import COVERED_LEAGUE_IDS
from src.ingestion.client import APIFootballClient, calls_used_today
from src.storage.db import get_session
from src.storage.models import Injury

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _target_fixtures() -> list[tuple[int, int, int]]:
    """Returns (fixture_id, league_id, season) for settled covered-league fixtures without an injuries row."""
    ids = ",".join(map(str, COVERED_LEAGUE_IDS))
    with get_session() as s:
        rows = s.execute(text(f"""
            SELECT DISTINCT f.id, f.league_id, f.season FROM fixtures f
            JOIN prediction_records pr ON pr.fixture_id = f.id
            WHERE pr.settled = 1 AND f.league_id IN ({ids})
              AND f.id NOT IN (SELECT DISTINCT fixture_id FROM injuries WHERE fixture_id IS NOT NULL)
        """)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def main() -> None:
    targets = _target_fixtures()
    logger.info(f"Fixtures needing injuries fetch: {len(targets)}")

    client = APIFootballClient()
    before = calls_used_today()
    total_rows = 0
    fixtures_with_data = 0

    for fixture_id, league_id, season in targets:
        try:
            raw = client.get("injuries", {"fixture": fixture_id})
        except Exception as exc:
            logger.warning(f"injuries fetch failed for fixture {fixture_id}: {exc}")
            continue

        if raw:
            fixtures_with_data += 1

        with get_session() as s:
            for entry in raw or []:
                player = entry.get("player") or {}
                team = entry.get("team") or {}
                player_id = player.get("id")
                if not player_id:
                    continue
                s.add(Injury(
                    player_id=player_id,
                    player_name=player.get("name") or "Unknown",
                    player_position=None,
                    fixture_id=fixture_id,
                    team_id=team.get("id"),
                    type=player.get("type", "Unknown"),
                    status="reported",
                    start_date=datetime.utcnow(),
                    reason=player.get("reason"),
                    league_id=league_id,
                    season=season,
                    fetched_at=datetime.utcnow(),
                ))
                total_rows += 1
            s.commit()

    after = calls_used_today()
    logger.info(
        f"Done. Calls used: {after - before}. Fixtures with >=1 injury row: {fixtures_with_data}/{len(targets)}. "
        f"Injury rows inserted: {total_rows}."
    )


if __name__ == "__main__":
    main()
