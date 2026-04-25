#!/usr/bin/env python3
"""
scripts/live_stats_collector.py

Collect live match statistics and events during games.
Run continuously during match hours to build up training data
for in-play prediction models.

Usage:
    python scripts/live_stats_collector.py              # Run once
    python scripts/live_stats_collector.py --continuous # Run continuously
    python scripts/live_stats_collector.py --interval 30 # Check every 30 seconds
"""
import argparse
import logging
import sys
import time
from datetime import datetime

sys.path.insert(0, '/opt/projects/bootball')

from sqlalchemy import select

from config.leagues import LEAGUES
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Fixture, LiveMatchStats, MatchEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

LIVE_STATUSES = ["1H", "2H", "HT", "ET", "P"]


def get_live_fixtures(client: APIFootballClient) -> list[dict]:
    """Get currently live matches across all leagues."""
    live_fixtures = []

    for lid in LEAGUES:
        try:
            fixtures = client.get_fixtures(league_id=lid, season=2025, status=",".join(LIVE_STATUSES))
            if fixtures:
                live_fixtures.extend(fixtures)
        except Exception as e:
            logger.debug(f"Error fetching live fixtures for league {lid}: {e}")

    return live_fixtures


def collect_fixture_stats(client: APIFootballClient, fixture_data: dict) -> dict | None:
    """Collect statistics for a fixture."""
    fix_id = fixture_data.get("fixture", {}).get("id")
    if not fix_id:
        return None

    try:
        stats = client.get_fixture_statistics(fix_id)
        if not stats:
            return None

        stat_block = stats[0] if isinstance(stats, list) else stats
        return stat_block
    except Exception as e:
        logger.warning(f"Error fetching stats for fixture {fix_id}: {e}")
        return None


def collect_fixture_events(client: APIFootballClient, fixture_data: dict) -> list[dict]:
    """Collect events (goals, cards, subs) for a fixture."""
    fix_id = fixture_data.get("fixture", {}).get("id")
    if not fix_id:
        return []

    try:
        events = client.get_fixture_events(fix_id)
        return events or []
    except Exception as e:
        logger.warning(f"Error fetching events for fixture {fix_id}: {e}")
        return []


def parse_stats_to_model(fixture_id: int, stats: dict, elapsed: int, period: str) -> dict:
    """Parse statistics response into LiveMatchStats fields."""
    home_stats = stats.get("home", {}) if isinstance(stats, dict) else {}
    away_stats = stats.get("away", {}) if isinstance(stats, dict) else {}

    return {
        "fixture_id": fixture_id,
        "minute": elapsed,
        "period": period,
        "home_shots_total": home_stats.get("shots", {}).get("total", 0) or 0,
        "away_shots_total": away_stats.get("shots", {}).get("total", 0) or 0,
        "home_shots_on_target": home_stats.get("shots", {}).get("on", 0) or 0,
        "away_shots_on_target": away_stats.get("shots", {}).get("on", 0) or 0,
        "home_possession": home_stats.get("possession", "50") or 50.0,
        "away_possession": away_stats.get("possession", "50") or 50.0,
        "home_corners": home_stats.get("corners", 0) or 0,
        "away_corners": away_stats.get("corners", 0) or 0,
        "home_fouls": home_stats.get("fouls", 0) or 0,
        "away_fouls": away_stats.get("fouls", 0) or 0,
        "home_yellow_cards": home_stats.get("cards", {}).get("yellow", 0) or 0,
        "away_yellow_cards": away_stats.get("cards", {}).get("yellow", 0) or 0,
        "home_red_cards": home_stats.get("cards", {}).get("red", 0) or 0,
        "away_red_cards": away_stats.get("cards", {}).get("red", 0) or 0,
    }


def parse_event_to_model(fixture_id: int, event: dict) -> dict | None:
    """Parse event response into MatchEvent fields."""
    event_type = event.get("type", "")
    if event_type not in ["Goal", "Card", "Substitution", "Var"]:
        return None

    team_name = event.get("team", {}).get("name", "")
    is_home = event.get("team", {}).get("home", False)

    minute = event.get("time", {}).get("elapsed", 0)

    player_name = event.get("player", {}).get("name", "")
    result = event.get("assist", {}).get("name", "") if event_type == "Goal" else event.get("detail", "")

    type_map = {
        "Goal": "goal",
        "Card": "yellow_card" if "yellow" in (event.get("detail") or "").lower() else "red_card",
        "Substitution": "substitution",
        "Var": "var"
    }

    return {
        "fixture_id": fixture_id,
        "type": type_map.get(event_type, event_type.lower()),
        "minute": minute,
        "team": "home" if is_home else "away",
        "player_name": player_name,
        "result": result,
        "is_home": is_home,
    }


def store_live_stats(fixture_id: int, stats_data: dict, elapsed: int, period: str, home_goals: int, away_goals: int):
    """Store or update live match stats in database."""
    with get_session() as s:
        existing = s.execute(
            select(LiveMatchStats).where(
                LiveMatchStats.fixture_id == fixture_id,
                LiveMatchStats.minute == elapsed,
            )
        ).scalar_one_or_none()

        data = parse_stats_to_model(fixture_id, stats_data, elapsed, period)
        data["home_goals"] = home_goals
        data["away_goals"] = away_goals
        data["score_diff"] = home_goals - away_goals

        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
        else:
            s.add(LiveMatchStats(**data))
            s.commit()


def store_live_events(fixture_id: int, events: list[dict]):
    """Store new match events in database."""
    stored = 0
    with get_session() as s:
        for event in events:
            parsed = parse_event_to_model(fixture_id, event)
            if not parsed:
                continue

            existing = s.execute(
                select(MatchEvent).where(
                    MatchEvent.fixture_id == fixture_id,
                    MatchEvent.type == parsed["type"],
                    MatchEvent.minute == parsed["minute"],
                    MatchEvent.team == parsed["team"],
                )
            ).scalar_one_or_none()

            if not existing:
                s.add(MatchEvent(**parsed))
                stored += 1

        s.commit()
    return stored


def get_period_from_status(status: str) -> str:
    """Map API status to period string."""
    period_map = {
        "1H": "1H",
        "HT": "HT",
        "2H": "2H",
        "ET": "ET",
        "P": "P",
        "FT": "FT",
    }
    return period_map.get(status, status)


def process_live_fixture(client: APIFootballClient, fixture: dict):
    """Process a single live fixture: collect and store stats and events."""
    fix_data = fixture.get("fixture", {})
    teams_data = fixture.get("teams", {})
    goals_data = fixture.get("goals", {})
    status_data = fixture.get("fixture", {}).get("status", {})

    fix_id = fix_data.get("id")
    if not fix_id:
        return

    status = status_data.get("short", "") if isinstance(status_data, dict) else status_data
    elapsed = status_data.get("elapsed", 0) if isinstance(status_data, dict) else 0
    period = get_period_from_status(status)

    home_goals = goals_data.get("home") or 0
    away_goals = goals_data.get("away") or 0

    logger.debug(f"Processing fixture {fix_id}: {teams_data.get('home', {}).get('name', 'Home')} vs {teams_data.get('away', {}).get('name', 'Away')} - {status} {elapsed}'")

    stats_data = collect_fixture_stats(client, fixture)
    if stats_data:
        store_live_stats(fix_id, stats_data, elapsed, period, home_goals, away_goals)

    events = collect_fixture_events(client, fixture)
    if events:
        stored = store_live_events(fix_id, events)
        if stored > 0:
            logger.info(f"Stored {stored} new events for fixture {fix_id}")

    update_fixture_goals(fix_id, home_goals, away_goals, status)


def update_fixture_goals(fixture_id: int, home_goals: int, away_goals: int, status: str):
    """Update fixture goals in database if changed."""
    with get_session() as s:
        fixture = s.execute(
            select(Fixture).where(Fixture.id == fixture_id)
        ).scalar_one_or_none()

        if fixture:
            needs_commit = False
            if fixture.goals_home != home_goals:
                fixture.goals_home = home_goals
                needs_commit = True
            if fixture.goals_away != away_goals:
                fixture.goals_away = away_goals
                needs_commit = True
            if fixture.status != status:
                fixture.status = status
                needs_commit = True

            if needs_commit:
                s.commit()


def collect_live_stats(client: APIFootballClient):
    """Collect stats for all live fixtures."""
    live_fixtures = get_live_fixtures(client)

    if not live_fixtures:
        logger.info("No live matches found")
        return 0

    logger.info(f"Collecting stats for {len(live_fixtures)} live matches...")

    for fixture in live_fixtures:
        try:
            process_live_fixture(client, fixture)
        except Exception as e:
            logger.error(f"Error processing fixture: {e}")

    return len(live_fixtures)


def run_collector(continuous: bool = False, interval: int = 30):
    """Run the live stats collector."""
    client = APIFootballClient()

    if continuous:
        logger.info(f"Starting continuous live stats collection (interval: {interval}s)")
        while True:
            collect_live_stats(client)
            time.sleep(interval)
    else:
        collect_live_stats(client)


def main():
    parser = argparse.ArgumentParser(description="Live match stats collector")
    parser.add_argument("--continuous", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds")
    args = parser.parse_args()

    run_collector(continuous=args.continuous, interval=args.interval)


if __name__ == "__main__":
    main()