"""
scripts/analysis/phase37_backfill_covered_2026.py

Phase 37 Part A.3 — season-2026 player_season_stats backfill, SCOPED to
config.covered_leagues.COVERED_LEAGUE_IDS only (unlike
scripts/backfill_player_stats.py's run_backfill(), which pulls every team
across all ~875 leagues for a season). Reuses the same storage helpers.

Projected cost stated before running: len(missing covered teams) calls,
1 call per team via GET /players?team=X&season=2026.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from config.covered_leagues import COVERED_LEAGUE_IDS
from scripts.backfill_player_stats import _store_player_stats
from src.ingestion.client import APIFootballClient, calls_used_today
from src.storage.db import get_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SEASON = 2026


def _covered_teams() -> list[int]:
    ids = ",".join(map(str, COVERED_LEAGUE_IDS))
    with get_session() as s:
        rows = s.execute(text(f"""
            SELECT DISTINCT team_id FROM (
                SELECT home_team_id AS team_id FROM fixtures WHERE season={SEASON} AND league_id IN ({ids})
                UNION
                SELECT away_team_id AS team_id FROM fixtures WHERE season={SEASON} AND league_id IN ({ids})
            )
        """)).fetchall()
    return [r[0] for r in rows]


def _done_teams() -> set[int]:
    with get_session() as s:
        rows = s.execute(
            text("SELECT team_id FROM player_fetch_log WHERE season=:s"), {"s": SEASON}
        ).fetchall()
    return {r[0] for r in rows}


def main() -> None:
    teams = _covered_teams()
    done = _done_teams()
    missing = [t for t in teams if t not in done]
    logger.info(f"Covered teams: {len(teams)}, already fetched: {len(teams) - len(missing)}, missing: {len(missing)}")

    client = APIFootballClient()
    before = calls_used_today()
    total_inserted = 0

    for team_id in missing:
        try:
            raw = client.get_players(team_id=team_id, season=SEASON)
        except Exception as exc:
            logger.warning(f"get_players({team_id}, {SEASON}) failed: {exc}")
            continue

        inserted = 0
        if raw:
            for entry in raw:
                player_info = entry.get("player") or {}
                player_id = player_info.get("id")
                stats_list = entry.get("statistics") or []
                if player_id and stats_list:
                    inserted += _store_player_stats(player_id, team_id, SEASON, player_info, stats_list)

        with get_session() as s:
            s.execute(
                text("""INSERT OR REPLACE INTO player_fetch_log (team_id, season, fetched_at, row_count)
                       VALUES (:tid, :sea, :ts, :rc)"""),
                {"tid": team_id, "sea": SEASON, "ts": datetime.utcnow(), "rc": inserted},
            )
            s.commit()
        total_inserted += inserted

    after = calls_used_today()
    logger.info(f"Done. Calls used: {after - before}. Rows inserted: {total_inserted}.")


if __name__ == "__main__":
    main()
