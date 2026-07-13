"""
scripts/covered_injuries_poll.py

Phase 37 Part A.4 — forward daily injuries/availability collection for
covered-league NS fixtures (config.covered_leagues.COVERED_LEAGUE_IDS only).
Feeds the availability-tier features (Part B); confirmed lineups near kickoff
are a separate, Part-C-gated concern (see backend/scheduler.py's docstring
on job_fetch_covered_injuries).

One call per (league, date) pair with an NS fixture in the next
FORWARD_HORIZON_DAYS -- covers every fixture on that league/date in a single
call (confirmed empirically: injuries?league=X&date=Y and injuries?fixture=Z
return identical results when Z is the only fixture on that date/league; the
league+date form batches naturally when there are several). At the current
12-league covered set this runs to single digits of calls/day in most windows
-- Phase 36's ~50-100/day estimate was for a larger hypothetical covered set
before Part 37's probe narrowed it down.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

from config.covered_leagues import COVERED_LEAGUE_IDS
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Injury

logger = logging.getLogger(__name__)

FORWARD_HORIZON_DAYS = 7


def find_league_date_pairs_needing_injuries(session) -> list[tuple[int, str]]:
    """(league_id, date) pairs with an NS fixture in the forward horizon in a covered league."""
    ids = ",".join(map(str, COVERED_LEAGUE_IDS))
    rows = session.execute(text(f"""
        SELECT DISTINCT league_id, date(date) as d FROM fixtures
        WHERE status='NS' AND league_id IN ({ids})
          AND date >= datetime('now') AND date < datetime('now', '+{FORWARD_HORIZON_DAYS} days')
    """)).fetchall()
    return [(r[0], r[1]) for r in rows]


def poll_and_store_injuries(session, client: APIFootballClient, pairs: list[tuple[int, str]]) -> int:
    """Fetch + upsert injuries for each (league, date) pair. Returns rows written."""
    total = 0
    for league_id, date_str in pairs:
        try:
            raw = client.get_injuries(league_id=league_id, date=date_str)
        except Exception:
            logger.exception("covered_injuries_poll: fetch failed for league=%s date=%s", league_id, date_str)
            continue

        for entry in raw or []:
            player = entry.get("player") or {}
            team = entry.get("team") or {}
            fixture = entry.get("fixture") or {}
            league = entry.get("league") or {}
            player_id = player.get("id")
            fixture_id = fixture.get("id")
            if not player_id or not fixture_id:
                continue

            # Idempotent per (player, fixture): forward polling re-runs daily
            # while a fixture stays NS, so skip rows already captured.
            existing = session.execute(
                text("SELECT id FROM injuries WHERE player_id=:pid AND fixture_id=:fid"),
                {"pid": player_id, "fid": fixture_id},
            ).fetchone()
            if existing:
                continue

            session.add(Injury(
                player_id=player_id,
                player_name=player.get("name") or "Unknown",
                player_position=None,
                fixture_id=fixture_id,
                team_id=team.get("id"),
                type=player.get("type", "Unknown"),
                status="reported",
                start_date=datetime.utcnow(),
                reason=player.get("reason"),
                league_id=league.get("id") or league_id,
                season=league.get("season"),
                fetched_at=datetime.utcnow(),
            ))
            total += 1

    session.commit()
    return total


def run() -> int:
    client = APIFootballClient()
    with get_session() as s:
        pairs = find_league_date_pairs_needing_injuries(s)
        if not pairs:
            return 0
        return poll_and_store_injuries(s, client, pairs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    written = run()
    logger.info(f"covered_injuries_poll: {written} injury rows written")
