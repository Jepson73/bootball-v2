"""
src/handlers/backfill_handler.py

Handles backfill of historic and short-term data.
Supports both full historic backfill and incremental (2h-24h) backfill.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.events.base import EventEmitter, EventType, get_emitter
from src.events.fixture_events import FixtureScheduled
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Fixture, Team

logger = logging.getLogger(__name__)


class BackfillHandler:
    """Handles backfill of fixture and odds data.

    Two modes:
    1. Historic backfill: Fetch missed data for date ranges (e.g., lost data)
    2. Short-term backfill: Fetch recent fixtures (2h-24h) that were missed

    Events consumed:
    - None (triggered manually or by scheduler)

    Events emitted:
    - FixtureScheduled (when new fixture found during backfill)
    """

    def __init__(
        self,
        emitter: EventEmitter | None = None,
        client: APIFootballClient | None = None,
    ):
        self.emitter = emitter or get_emitter()
        self.client = client or APIFootballClient()
        self._processed_count = 0

    def backfill_date_range(
        self,
        league_id: int,
        start_date: datetime,
        end_date: datetime,
        status: str | None = None,
    ) -> int:
        """Backfill fixtures for a date range.

        Args:
            league_id: League to backfill
            start_date: Start of date range
            end_date: End of date range
            status: Optional status filter (e.g., "FT", "NS")

        Returns:
            Number of fixtures processed
        """
        self._processed_count = 0
        season = start_date.year

        logger.info(f"Backfilling league {league_id} from {start_date.date()} to {end_date.date()}")

        try:
            raw_fixtures = self.client.get_fixtures(
                league_id=league_id,
                season=season,
                from_date=start_date.strftime("%Y-%m-%d"),
                to_date=end_date.strftime("%Y-%m-%d"),
                status=status,
            )

            for raw in raw_fixtures:
                self._process_fixture_raw(raw, season)
                self._processed_count += 1

            logger.info(f"Backfill complete: {self._processed_count} fixtures processed")

        except Exception as e:
            logger.error(f"Backfill error for league {league_id}: {e}")

        return self._processed_count

    def backfill_short_term(
        self,
        hours: int = 24,
        leagues: list[int] | None = None,
    ) -> int:
        """Backfill recently completed/missed fixtures.

        This handles the case where:
        1. settle_fixtures didn't run
        2. Fixtures completed while system was down
        3. Odds weren't fetched when they should have been

        Args:
            hours: How far back to look (default 24)
            leagues: Optional list of league IDs to backfill

        Returns:
            Number of fixtures processed
        """
        from config.leagues import ALL_LEAGUE_IDS

        self._processed_count = 0
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)

        target_leagues = leagues or ALL_LEAGUE_IDS

        logger.info(f"Short-term backfill: last {hours} hours")

        for league_id in target_leagues:
            try:
                count = self.backfill_date_range(
                    league_id=league_id,
                    start_date=start,
                    end_date=now,
                    status="FT",  # Only fetch completed
                )
                logger.info(f"  League {league_id}: {count} fixtures")

            except Exception as e:
                logger.warning(f"  League {league_id} backfill error: {e}")

        logger.info(f"Short-term backfill complete: {self._processed_count} fixtures")
        return self._processed_count

    def backfill_fixtures_for_season(
        self,
        league_id: int,
        season: int,
    ) -> int:
        """Backfill entire season for a league.

        Args:
            league_id: League to backfill
            season: Season year (e.g., 2025 for 2024-2025 season)

        Returns:
            Number of fixtures processed
        """
        # Typical season spans ~10 months
        start_date = datetime(season, 7, 1, tzinfo=timezone.utc)
        end_date = datetime(season + 1, 5, 31, tzinfo=timezone.utc)

        return self.backfill_date_range(league_id, start_date, end_date)

    def _process_fixture_raw(self, raw: dict, season: int) -> None:
        """Process a raw fixture dict from API.

        Args:
            raw: Raw fixture data from API-Football
            season: Season year
        """
        fixture = raw.get("fixture", {})
        teams = raw.get("teams", {})
        league = raw.get("league", {})

        fixture_id = fixture.get("id")
        if not fixture_id:
            return

        home_team_id = teams.get("home", {}).get("id")
        away_team_id = teams.get("away", {}).get("id")
        league_id = league.get("id")

        if not all([fixture_id, home_team_id, away_team_id, league_id]):
            return

        date_str = fixture.get("date")
        fixture_date = (
            datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if date_str
            else datetime.now(timezone.utc)
        )

        with get_session() as s:
            # Check if fixture exists
            existing = s.execute(
                select(Fixture).where(Fixture.id == fixture_id)
            ).scalars().first()

            if existing:
                # Update if needed
                if existing.status != "FT" and fixture.get("status") == "FT":
                    existing.status = "FT"
                    existing.goals_home = raw.get("goals", {}).get("home")
                    existing.goals_away = raw.get("goals", {}).get("away")
                    logger.debug(f"Updated fixture {fixture_id} to FT")
            else:
                # Create new fixture
                home_name = teams.get("home", {}).get("name", "Home")
                away_name = teams.get("away", {}).get("name", "Away")

                team_names = {home_team_id: home_name, away_team_id: away_name}

                # Ensure teams exist
                for tid, tname in team_names.items():
                    existing_team = s.execute(
                        select(Team).where(Team.id == tid)
                    ).scalars().first()
                    if not existing_team:
                        s.add(Team(id=tid, name=tname))

                s.add(Fixture(
                    id=fixture_id,
                    league_id=league_id,
                    season=season,
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    date=fixture_date,
                    status=fixture.get("status", "NS"),
                    goals_home=raw.get("goals", {}).get("home"),
                    goals_away=raw.get("goals", {}).get("away"),
                ))

                # Emit FixtureScheduled event
                emit_fixture_scheduled(
                    fixture_id=fixture_id,
                    league_id=league_id,
                    home_id=home_team_id,
                    away_id=away_team_id,
                    date=fixture_date,
                )

            s.commit()


def emit_fixture_scheduled(
    fixture_id: int,
    league_id: int,
    home_id: int,
    away_id: int,
    date: datetime,
) -> FixtureScheduled:
    """Emit FixtureScheduled event."""
    from src.events.fixture_events import FixtureScheduled
    event = FixtureScheduled(
        payload={
            "fixture_id": fixture_id,
            "league_id": league_id,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "date": date.isoformat() if date else None,
        }
    )
    get_emitter().emit(event)
    return event
