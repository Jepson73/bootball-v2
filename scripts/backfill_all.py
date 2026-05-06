#!/usr/bin/env python3
"""
scripts/backfill_all.py

Backfills fixtures, stats, events, and standings for all PRIORITY_LEAGUE_IDS
(or specific leagues via --leagues).  Historical odds are not available from
the API and are skipped.

Usage:
    python scripts/backfill_all.py                                   # all leagues, all seasons
    python scripts/backfill_all.py --seasons 2020                    # specific season
    python scripts/backfill_all.py --leagues 188 --seasons 2021 2022 # single league
    python scripts/backfill_all.py --dry-run                         # test without API calls
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.leagues import (
    PRIORITY_LEAGUE_IDS,
    BACKFILL_SEASONS, LEAGUES
)
from config.settings import settings
from src.ingestion.client import APIFootballClient, calls_remaining_today, calls_used_today
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureEvent, FixtureOdds, FixtureStats,
    League, Standing, Team, Player
)
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class EuropeanBackfiller:
    def __init__(self, client: APIFootballClient | None = None):
        self.client = client or APIFootballClient()
        self.stats = {
            "leagues_processed": 0,
            "fixtures_loaded": 0,
            "teams_loaded": 0,
            "players_loaded": 0,
            "stats_loaded": 0,
            "odds_loaded": 0,
            "api_calls_used": 0,
        }
    
    def _upsert_league(self, session, league_id: int) -> None:
        meta = LEAGUES.get(league_id, {})
        existing = session.get(League, league_id)
        if not existing:
            session.add(League(
                id=league_id,
                name=meta.get("name", str(league_id)),
                country=meta.get("country", "Unknown"),
            ))
    
    def _backfill_players(self, session, team_ids: set, season: int) -> None:
        """Backfill player data for teams."""
        logger.info(f"  Fetching player data for {len(team_ids)} teams...")
        
        count = 0
        for team_id in team_ids:
            if not team_id:
                continue
            try:
                players = self.client.get_players(team_id=team_id, season=season)
                if not players:
                    continue
                    
                for p in players:
                    player = p.get("player", {})
                    pid = player.get("id")
                    if not pid:
                        continue
                    
                    stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    games = stats.get("games", {})
                    
                    existing = session.get(Player, pid)
                    if existing:
                        continue
                        
                    session.add(Player(
                        id=pid,
                        team_id=team_id,
                        name=player.get("name", ""),
                        position=games.get("position"),
                        photo_url=player.get("photo"),
                        goals=stats.get("goals", {}).get("total", 0),
                        assists=stats.get("goals", {}).get("assists", 0),
                        yellow_cards=stats.get("cards", {}).get("yellow", 0),
                        red_cards=stats.get("cards", {}).get("red", 0),
                        minutes_played=games.get("minutes", 0),
                        updated_at=datetime.utcnow(),
                    ))
                    count += 1
                    
            except Exception:
                continue
        
        try:
            session.commit()
        except Exception:
            pass
        
        logger.info(f"    Loaded {count} players")
        
        logger.info(f"    Loaded {self.stats['players_loaded']} players")
    
    def _upsert_team(self, session, team_data: dict) -> None:
        t = team_data.get("team", team_data)
        team_id = t.get("id")
        if not team_id:
            return
        from sqlalchemy import select
        existing = session.execute(
            select(Team).where(Team.id == team_id)
        ).first()
        if not existing:
            session.add(Team(
                id=team_id,
                name=t.get("name", ""),
                code=t.get("code"),
                country=t.get("country"),
                logo_url=t.get("logo"),
            ))
            self.stats["teams_loaded"] += 1
    
    def _parse_fixture(self, raw: dict) -> dict:
        f = raw.get("fixture", {})
        teams = raw.get("teams", {})
        goals = raw.get("goals", {})
        
        home_goals = goals.get("home")
        away_goals = goals.get("away")
        
        if home_goals is not None and away_goals is not None:
            if home_goals > away_goals:
                outcome = "H"
            elif home_goals < away_goals:
                outcome = "A"
            else:
                outcome = "D"
        else:
            outcome = None
        
        date_str = f.get("date")
        if date_str:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            date = None
        
        return dict(
            id=f.get("id"),
            venue=f.get("venue", {}).get("name") if isinstance(f.get("venue"), dict) else None,
            referee=f.get("referee"),
            status=f.get("status", {}).get("short"),
            date=date,
            home_team_id=teams.get("home", {}).get("id"),
            away_team_id=teams.get("away", {}).get("id"),
            goals_home=home_goals,
            goals_away=away_goals,
            ht_goals_home=raw.get("score", {}).get("halftime", {}).get("home"),
            ht_goals_away=raw.get("score", {}).get("halftime", {}).get("away"),
            outcome=outcome,
        )
    
    def backfill_league_season(
        self,
        league_id: int,
        season: int,
        include_odds: bool = True,
        include_stats: bool = True,
    ) -> dict:
        """Backfill a single league/season combination."""
        league_name = LEAGUES.get(league_id, {}).get("name", str(league_id))
        logger.info(f"Backfilling {league_name} {season}...")
        
        if calls_remaining_today() < 20:
            logger.warning("Low on API calls, stopping")
            return {"status": "skipped", "reason": "low_api_calls"}
        
        # Skip if we already have a good number of fixtures for this season
        with get_session() as _s:
            existing_count = _s.execute(
                select(Fixture)
                .where(Fixture.league_id == league_id)
                .where(Fixture.season == season)
            ).scalars().all()
            existing_ids = {f.id for f in existing_count}

        # Get all finished fixtures (1 call)
        raw_fixtures = self.client.get_fixtures(
            league_id=league_id,
            season=season,
            status="FT",
        )

        if not raw_fixtures:
            logger.warning(f"No finished fixtures for {league_id} {season}")
            return {"status": "skipped", "reason": "no_fixtures"}

        fixture_ids = [r["fixture"]["id"] for r in raw_fixtures]
        logger.info(f"  Found {len(fixture_ids)} fixtures ({len(existing_ids)} already in DB)")

        # If all fixtures are already loaded, only fetch missing stats/odds
        new_fixture_ids = [fid for fid in fixture_ids if fid not in existing_ids]
        logger.info(f"  New fixtures to insert: {len(new_fixture_ids)}")
        
        # Batch fetch only new fixtures (N/20 calls)
        if new_fixture_ids:
            full_fixtures = self.client.get_fixtures_batch(new_fixture_ids)
        else:
            full_fixtures = []
            logger.info("  All fixtures already in DB, skipping batch fetch")
        
        # Insert data
        with get_session() as session:
            self._upsert_league(session, league_id)
            
            # Get existing team IDs to avoid duplicates
            existing_teams = set(row[0] for row in session.execute(select(Team.id)).fetchall())
            
            team_ids_seen = set()
            teams_to_add = []
            
            for raw in full_fixtures:
                for side in ["home", "away"]:
                    team_raw = raw.get("teams", {}).get(side, {})
                    t = team_raw.get("team", team_raw)
                    team_id = t.get("id")
                    if team_id and team_id not in team_ids_seen and team_id not in existing_teams:
                        team_ids_seen.add(team_id)
                        teams_to_add.append(Team(
                            id=team_id,
                            name=t.get("name", ""),
                            code=t.get("code"),
                            country=t.get("country"),
                            logo_url=t.get("logo"),
                        ))
            
            if teams_to_add:
                session.add_all(teams_to_add)
            
            session.commit()  # Commit teams first
            
            # Backfill player data for teams in this league
            self._backfill_players(session, team_ids_seen, season)
            
            for raw in full_fixtures:
                parsed = self._parse_fixture(raw)
                fid = parsed["id"]
                if not fid:
                    continue
                
                existing = session.get(Fixture, fid)
                if not existing:
                    session.add(Fixture(
                        **parsed,
                        league_id=league_id,
                        season=season,
                        round=raw.get("league", {}).get("round"),
                    ))
                    self.stats["fixtures_loaded"] += 1
                    
                    for ev in raw.get("events", []):
                        session.add(FixtureEvent(
                            fixture_id=fid,
                            minute=ev.get("time", {}).get("elapsed"),
                            team_id=ev.get("team", {}).get("id"),
                            player_name=ev.get("player", {}).get("name"),
                            event_type=ev.get("type"),
                            detail=ev.get("detail"),
                        ))
        
        # Fetch stats — skip fixtures that already have stats loaded
        if include_stats:
            with get_session() as _s:
                stats_have = set(
                    row[0] for row in _s.execute(
                        select(FixtureStats.fixture_id)
                        .where(FixtureStats.fixture_id.in_(fixture_ids))
                        .distinct()
                    ).fetchall()
                )
            stats_needed = [fid for fid in fixture_ids if fid not in stats_have]
            logger.info(f"  Fetching stats for {len(stats_needed)}/{len(fixture_ids)} fixtures (skipping {len(stats_have)} with existing stats)...")
            for i, fid in enumerate(stats_needed):
                if i % 50 == 0:
                    logger.info(f"    Progress: {i}/{len(stats_needed)}")

                raw_stats = self.client.get_fixture_statistics(fid)
                if raw_stats:
                    self._parse_and_store_stats(fid, raw_stats)
                    self.stats["stats_loaded"] += 1
        
        # Fetch odds (N calls) - only if requested
        if include_odds:
            # Use the actual fixtures stored, not the original list
            # (batch fetch may skip some fixtures)
            stored_fixture_ids = [
                r["fixture"]["id"] for r in full_fixtures if r.get("fixture", {}).get("id")
            ]
            logger.info(f"  Fetching odds for {len(stored_fixture_ids)} fixtures...")
            for i, fid in enumerate(stored_fixture_ids):
                if i % 50 == 0:
                    logger.info(f"    Progress: {i}/{len(stored_fixture_ids)}")
                
                raw_odds = self.client.get_odds(fixture_id=fid, bet_type=1)
                if raw_odds:
                    self._parse_and_store_odds(fid, raw_odds)
                    self.stats["odds_loaded"] += 1
        
        # Get standings (1 call)
        raw_standings = self.client.get_standings(league_id, season)
        if raw_standings:
            self._parse_and_store_standings(league_id, season, raw_standings)
        
        self.stats["leagues_processed"] += 1
        
        return {
            "status": "success",
            "fixtures": len(fixture_ids),
            "api_calls_remaining": calls_remaining_today(),
        }
    
    def _parse_and_store_stats(self, fixture_id: int, raw_stats: list) -> None:
        def val(stats_list: list, stat_name: str):
            for s in stats_list:
                if s.get("type") == stat_name:
                    v = s.get("value")
                    if v is None:
                        return None
                    if isinstance(v, str) and v.endswith("%"):
                        return float(v.rstrip("%"))
                    try:
                        return int(v)
                    except (ValueError, TypeError):
                        return None
            return None
        
        home_stats = next((t.get("statistics", []) for t in raw_stats if raw_stats.index(t) == 0), [])
        away_stats = next((t.get("statistics", []) for t in raw_stats if raw_stats.index(t) == 1), [])
        
        stats_data = dict(
            fixture_id=fixture_id,
            home_shots_total=val(home_stats, "Total Shots"),
            away_shots_total=val(away_stats, "Total Shots"),
            home_shots_on_goal=val(home_stats, "Shots on Goal"),
            away_shots_on_goal=val(away_stats, "Shots on Goal"),
            home_possession=val(home_stats, "Ball Possession"),
            away_possession=val(away_stats, "Ball Possession"),
            home_corners=val(home_stats, "Corner Kicks"),
            away_corners=val(away_stats, "Corner Kicks"),
            home_yellow_cards=val(home_stats, "Yellow Cards"),
            away_yellow_cards=val(away_stats, "Yellow Cards"),
            home_red_cards=val(home_stats, "Red Cards"),
            away_red_cards=val(away_stats, "Red Cards"),
            home_passes_total=val(home_stats, "Total passes"),
            away_passes_total=val(away_stats, "Total passes"),
            home_passes_accurate=val(home_stats, "Passes accurate"),
            away_passes_accurate=val(away_stats, "Passes accurate"),
        )
        
        with get_session() as session:
            existing = session.execute(
                select(FixtureStats).where(FixtureStats.fixture_id == fixture_id)
            ).first()
            if not existing:
                session.add(FixtureStats(**stats_data))
    
    def _parse_and_store_odds(self, fixture_id: int, raw_odds: list) -> None:
        with get_session() as session:
            for bm in raw_odds:
                bookmaker = bm.get("bookmakers", [{}])[0].get("name", "unknown")
                bets = bm.get("bookmakers", [{}])[0].get("bets", [])
                
                for bet in bets:
                    if bet.get("name") != "Match Winner":
                        continue
                    
                    values = {v["value"]: float(v["odd"]) for v in bet.get("values", [])}
                    
                    existing = session.execute(
                        select(FixtureOdds).where(
                            FixtureOdds.fixture_id == fixture_id,
                            FixtureOdds.bookmaker == bookmaker,
                            FixtureOdds.bet_type == "h2h",
                        )
                    ).first()
                    
                    if not existing:
                        session.add(FixtureOdds(
                            fixture_id=fixture_id,
                            bookmaker=bookmaker,
                            bet_type="h2h",
                            odd_home=values.get("Home"),
                            odd_draw=values.get("Draw"),
                            odd_away=values.get("Away"),
                        ))
    
    def _parse_and_store_standings(self, league_id: int, season: int, raw: list) -> None:
        try:
            league_standings = raw[0]["league"]["standings"][0]
        except (IndexError, KeyError):
            return
        
        with get_session() as session:
            for entry in league_standings:
                team = entry.get("team", {})
                all_ = entry.get("all", {})
                goals = all_.get("goals", {})
                
                team_id = team.get("id")
                if not team_id:
                    continue
                
                existing = session.execute(
                    select(Standing).where(
                        Standing.league_id == league_id,
                        Standing.season == season,
                        Standing.team_id == team_id,
                    )
                ).first()
                
                if existing:
                    existing[0].rank = entry.get("rank")
                    existing[0].points = entry.get("points")
                    existing[0].played = all_.get("played")
                    existing[0].won = all_.get("win")
                    existing[0].drawn = all_.get("draw")
                    existing[0].lost = all_.get("lose")
                    existing[0].goals_for = goals.get("for")
                    existing[0].goals_against = goals.get("against")
                    existing[0].goal_diff = entry.get("goalsDiff")
                else:
                    session.add(Standing(
                        league_id=league_id,
                        season=season,
                        team_id=team_id,
                        team_name=team.get("name", ""),
                        rank=entry.get("rank"),
                        points=entry.get("points"),
                        played=all_.get("played"),
                        won=all_.get("win"),
                        drawn=all_.get("draw"),
                        lost=all_.get("lose"),
                        goals_for=goals.get("for"),
                        goals_against=goals.get("against"),
                        goal_diff=entry.get("goalsDiff"),
                    ))
    
    def run(
        self,
        seasons: list[int],
        include_odds: bool = True,
        start_from_league: int | None = None,
        start_from_season: int | None = None,
        leagues: list[int] | None = None,
        stop_at_remaining: int = 30,
    ) -> None:
        """Backfill fixtures, stats, and standings for the given seasons.

        leagues: specific league IDs to process. Defaults to PRIORITY_LEAGUE_IDS.
        """
        league_ids = list(leagues) if leagues else list(PRIORITY_LEAGUE_IDS)
        logger.info(f"Backfilling {len(league_ids)} leagues × {len(seasons)} seasons")
        logger.info(f"Leagues: {league_ids}")
        logger.info(f"Seasons: {seasons}")
        logger.info(f"Include odds: {include_odds}")
        calls_at_start = calls_used_today()
        logger.info(f"API calls used today: {calls_at_start} / {settings.api_calls_per_day}")
        logger.info(f"API calls remaining: {calls_remaining_today()}")

        init_db()

        # Resume support: skip leagues before the resume point
        if start_from_league is not None:
            if start_from_league in league_ids:
                idx = league_ids.index(start_from_league)
                skipped = idx
                league_ids = league_ids[idx:]
                logger.info(f"Resuming from league {start_from_league} (skipped {skipped} leagues)")
            else:
                logger.warning(f"--start-from-league {start_from_league} not found in league list, starting from beginning")

        total = len(league_ids) * len(seasons)
        logger.info(f"Total jobs: {len(league_ids)} leagues × {len(seasons)} seasons = {total}")

        budget_exhausted = False
        for i, league_id in enumerate(league_ids):
            for j, season in enumerate(seasons):
                # Skip seasons before the resume point for the first league only
                if start_from_league is not None and league_id == start_from_league:
                    if start_from_season is not None and season < start_from_season:
                        logger.info(f"Skipping {league_id} {season} (before resume season)")
                        continue

                logger.info(f"\n--- Progress: {i+1}/{len(league_ids)} leagues | calls used this run: {calls_used_today() - calls_at_start} ---")

                if calls_remaining_today() < stop_at_remaining:
                    logger.warning(f"API budget low ({calls_remaining_today()} remaining, threshold={stop_at_remaining}), stopping")
                    budget_exhausted = True
                    break

                try:
                    result = self.backfill_league_season(
                        league_id, season,
                        include_odds=include_odds,
                        include_stats=True,
                    )
                    logger.info(f"Result: {result}")
                except Exception as e:
                    logger.error(f"Error on {league_id} {season}: {e}")
                    continue

            if budget_exhausted:
                logger.warning(f"Stopped at league_id={league_id} season={season} — resume with: --start-from-league {league_id} --start-from-season {season}")
                break

        calls_this_run = calls_used_today() - calls_at_start
        logger.info("\n" + "="*50)
        logger.info("BACKFILL COMPLETE")
        logger.info("="*50)
        logger.info(f"Leagues processed: {self.stats['leagues_processed']}")
        logger.info(f"Fixtures loaded: {self.stats['fixtures_loaded']}")
        logger.info(f"Teams loaded: {self.stats['teams_loaded']}")
        logger.info(f"Stats loaded: {self.stats['stats_loaded']}")
        logger.info(f"Odds loaded: {self.stats['odds_loaded']}")
        logger.info(f"API calls this run: {calls_this_run}")
        logger.info(f"API calls used today total: {calls_used_today()} / {settings.api_calls_per_day}")
        logger.info(f"API calls remaining: {calls_remaining_today()}")

        # Log completion to ingestion_log for process monitor
        try:
            from src.storage.db import get_session
            from sqlalchemy import text as _text
            summary = (f"leagues={self.stats['leagues_processed']} "
                       f"fixtures={self.stats['fixtures_loaded']} "
                       f"calls={calls_this_run}")
            with get_session() as _s:
                _s.execute(_text("""
                    INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message)
                    VALUES (:job, 1, :updated, :summary)
                """), {"job": "backfill_all", "updated": self.stats['fixtures_loaded'], "summary": summary})
                _s.commit()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Backfill fixtures + stats for all PRIORITY_LEAGUE_IDS (or specific leagues).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # All priority leagues, all configured seasons (default)
  python scripts/backfill_all.py

  # Specific seasons only
  python scripts/backfill_all.py --seasons 2020 2021

  # Single league, specific seasons
  python scripts/backfill_all.py --leagues 188 --seasons 2021 2022 2023

  # Resume an interrupted run
  python scripts/backfill_all.py --seasons 2020 --start-from-league 78
"""
    )
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=BACKFILL_SEASONS,
        help="Seasons to backfill (default: all BACKFILL_SEASONS from config)",
    )
    parser.add_argument(
        "--leagues",
        type=int,
        nargs="+",
        default=None,
        help="Specific league IDs to process (default: all PRIORITY_LEAGUE_IDS)",
    )
    parser.add_argument(
        "--all-leagues",
        action="store_true",
        help="Process all 1200+ leagues from config (overrides --leagues and PRIORITY_LEAGUE_IDS)",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only process leagues that have zero fixtures in the DB",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without making API calls",
    )
    parser.add_argument(
        "--start-from-league",
        type=int,
        default=None,
        help="Resume interrupted run: skip leagues before this ID",
    )
    parser.add_argument(
        "--start-from-season",
        type=int,
        default=None,
        help="For the resume league only: skip seasons before this year",
    )
    parser.add_argument(
        "--stop-at-remaining",
        type=int,
        default=30,
        help="Stop when API calls remaining drops below this value (default: 30)",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.warning("DRY RUN MODE - No API calls will be made")
        settings.dry_run = True

    # Resolve league list
    leagues_to_run = args.leagues
    if args.all_leagues or args.missing_only:
        from config.leagues import LEAGUES as ALL_LEAGUES
        leagues_to_run = sorted(ALL_LEAGUES.keys())
        if args.missing_only:
            from src.storage.db import get_session as _get_session
            from sqlalchemy import text as _text
            from src.ingestion.init_db import init_db as _init_db
            _init_db()
            with _get_session() as _s:
                in_db = {r[0] for r in _s.execute(_text("SELECT DISTINCT league_id FROM fixtures")).fetchall()}
            leagues_to_run = [lid for lid in leagues_to_run if lid not in in_db]
            logger.info(f"--missing-only: {len(leagues_to_run)} leagues have no fixtures in DB")

    backfiller = EuropeanBackfiller()
    backfiller.run(
        seasons=args.seasons,
        include_odds=False,  # Historical odds not available from API
        start_from_league=args.start_from_league,
        start_from_season=args.start_from_season,
        leagues=leagues_to_run,
        stop_at_remaining=args.stop_at_remaining,
    )


if __name__ == "__main__":
    main()