"""
src/ingestion/backfill.py

Resumable historical backfill.
Strategy:
  1. For each (league, season), fetch all fixture IDs (1 call)
  2. Batch-fetch fixtures in chunks of 20 (full data incl. events + lineups)
  3. Fetch stats for completed matches
  4. Fetch odds per fixture
  5. Skip anything already in DB

Call budget estimate per (league × season):
  - 1 call for fixture list
  - ceil(n_matches / 20) calls for batch fixture data  → ~19 calls for 380 matches
  - 1 call for standings
  Total ≈ ~21 calls per league-season

Full Tier 1 backfill (6 leagues × 5 seasons × 21 calls) = ~630 calls
Full 15-league backfill × 5 seasons = ~1,575 calls  (well within daily budget)
"""
from __future__ import annotations

import logging
from datetime import datetime

from config.leagues import LEAGUES, BACKFILL_SEASONS
from config.settings import settings
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureEvent, FixtureOdds, FixtureStats,
    League, Standing, Team,
)

logger = logging.getLogger(__name__)


class Backfiller:
    def __init__(self, client: APIFootballClient | None = None) -> None:
        self.client = client or APIFootballClient()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _upsert_league(self, session, league_id: int) -> None:
        meta = LEAGUES.get(league_id, {})
        existing = session.get(League, league_id)
        if not existing:
            session.add(League(
                id=league_id,
                name=meta.get("name", str(league_id)),
                country=meta.get("country", "Unknown"),
                tier=meta.get("tier", 3),
            ))

    def _upsert_teams_bulk(self, session, team_list: list[dict]) -> None:
        """Bulk insert teams, ignoring duplicates."""
        from sqlalchemy import select
        for team_data in team_list:
            t = team_data.get("team", team_data)
            team_id = t.get("id")
            if not team_id:
                continue
            existing = session.execute(
                select(Team).where(Team.id == team_id)
            ).first()
            if existing:
                continue
            session.add(Team(
                id=team_id,
                name=t.get("name", ""),
                code=t.get("code"),
                country=t.get("country"),
                logo_url=t.get("logo"),
            ))

    def _stats_exist(self, session, fixture_id: int) -> bool:
        from sqlalchemy import select
        return session.execute(
            select(FixtureStats).where(FixtureStats.fixture_id == fixture_id)
        ).first() is not None

    def _events_exist(self, session, fixture_id: int) -> bool:
        from sqlalchemy import select
        return session.execute(
            select(FixtureEvent).where(FixtureEvent.fixture_id == fixture_id)
        ).first() is not None

    def _odds_exist(self, session, fixture_id: int) -> bool:
        from sqlalchemy import select
        return session.execute(
            select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_id)
        ).first() is not None

    def get_data_coverage(self, league_id: int) -> dict:
        """Return coverage status for all data types for a league."""
        from sqlalchemy import select, func
        
        with get_session() as session:
            fixtures = session.execute(
                select(func.count(Fixture.id)).where(Fixture.league_id == league_id)
            ).scalar() or 0
            
            if fixtures == 0:
                return {'fixtures': 0}
            
            events = session.execute(
                select(func.count(FixtureEvent.id))
                .join(Fixture, Fixture.id == FixtureEvent.fixture_id)
                .where(Fixture.league_id == league_id)
            ).scalar() or 0
            
            stats = session.execute(
                select(func.count(FixtureStats.id))
                .join(Fixture, Fixture.id == FixtureStats.fixture_id)
                .where(Fixture.league_id == league_id)
            ).scalar() or 0
            
            odds = session.execute(
                select(func.count(FixtureOdds.id))
                .join(Fixture, Fixture.id == FixtureOdds.fixture_id)
                .where(Fixture.league_id == league_id)
            ).scalar() or 0
            
            return {
                'fixtures': fixtures,
                'events': events,
                'stats': stats,
                'odds': odds,
                'has_events': events > 0,
                'has_stats': stats > 0,
                'has_odds': odds > 0,
            }

    # ── Parsers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_fixture(raw: dict) -> dict:
        f = raw.get("fixture", {})
        teams = raw.get("teams", {})
        goals = raw.get("goals", {})
        score = raw.get("score", {})
        ht = score.get("halftime", {})

        home_goals = goals.get("home")
        away_goals = goals.get("away")
        outcome = None
        if home_goals is not None and away_goals is not None:
            if home_goals > away_goals:
                outcome = "H"
            elif home_goals < away_goals:
                outcome = "A"
            else:
                outcome = "D"

        date_str = f.get("date")
        date = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else None

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
            ht_goals_home=ht.get("home"),
            ht_goals_away=ht.get("away"),
            outcome=outcome,
        )

    @staticmethod
    def _parse_stats(fixture_id: int, raw_stats: list[dict]) -> dict:
        """raw_stats is the list of two team-stat objects [{team, statistics}, …]."""
        def val(stats_list: list[dict], stat_name: str) -> float | int | None:
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

        home_stats = next(
            (t.get("statistics", []) for t in raw_stats if t.get("team", {}).get("id") and
             raw_stats.index(t) == 0), []
        )
        away_stats = next(
            (t.get("statistics", []) for t in raw_stats if raw_stats.index(t) == 1), []
        )

        return dict(
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
            home_xg=val(home_stats, "expected_goals"),
            away_xg=val(away_stats, "expected_goals"),
        )

    # ── Main workers ──────────────────────────────────────────────────────

    def backfill_league_season(
        self,
        league_id: int,
        season: int,
        include_odds: bool = True,
    ) -> None:
        logger.info("Backfilling league=%d season=%d", league_id, season)

        if calls_remaining_today() < 50:
            logger.warning("Low on API calls (%d left). Stopping.", calls_remaining_today())
            return

        # Step 1: get all finished fixture IDs (1 call)
        raw_fixtures = self.client.get_fixtures(
            league_id=league_id,
            season=season,
            status="FT",
        )
        if not raw_fixtures:
            logger.warning("No finished fixtures found for league=%d season=%d", league_id, season)
            return

        fixture_ids = [r["fixture"]["id"] for r in raw_fixtures]
        logger.info("Found %d fixtures", len(fixture_ids))

        # Step 2: batch fetch full fixture data (ceil(n/20) calls)
        full_fixtures = self.client.get_fixtures_batch(fixture_ids)

        with get_session() as session:
            self._upsert_league(session, league_id)

            # Collect unique teams first to avoid duplicates
            team_ids_seen = set()
            teams_to_add = []
            
            for raw in full_fixtures:
                for side in ["home", "away"]:
                    team_raw = raw.get("teams", {}).get(side, {})
                    t = team_raw.get("team", team_raw)
                    team_id = t.get("id")
                    if team_id and team_id not in team_ids_seen:
                        team_ids_seen.add(team_id)
                        teams_to_add.append(Team(
                            id=team_id,
                            name=t.get("name", ""),
                            code=t.get("code"),
                            country=t.get("country"),
                            logo_url=t.get("logo"),
                        ))
            
            # Bulk add teams, checking for existing
            from sqlalchemy import select
            for team in teams_to_add:
                existing = session.execute(
                    select(Team).where(Team.id == team.id)
                ).first()
                if not existing:
                    session.add(team)
            
            # Now add fixtures
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
                else:
                    # This step only ever fetches status="FT" from the API, so any
                    # existing row seen here is confirmed finished. Correct fields
                    # that drifted (most commonly: a fixture stuck at status='NS'
                    # with a stale date because it was inserted before it kicked
                    # off and never revisited). Diff-then-write — only touch
                    # columns that actually changed, so idempotent re-runs are
                    # a no-op.
                    for field in ("status", "date", "goals_home", "goals_away",
                                  "ht_goals_home", "ht_goals_away", "outcome"):
                        new_val = parsed.get(field)
                        if new_val is not None and getattr(existing, field) != new_val:
                            setattr(existing, field, new_val)

                # Always save events (new or existing fixture)
                if not self._events_exist(session, fid):
                    for ev in raw.get("events", []):
                        session.add(FixtureEvent(
                            fixture_id=fid,
                            minute=ev.get("time", {}).get("elapsed"),
                            team_id=ev.get("team", {}).get("id"),
                            player_name=ev.get("player", {}).get("name"),
                            event_type=ev.get("type"),
                            detail=ev.get("detail"),
                        ))

        # Step 3: fetch stats for completed matches (1 call each, only if missing)
        with get_session() as session:
            missing_stats = [
                fid for fid in fixture_ids
                if not self._stats_exist(session, fid)
            ]

        for fid in missing_stats:
            raw_stats = self.client.get_fixture_statistics(fid)
            if raw_stats:
                parsed_stats = self._parse_stats(fid, raw_stats)
                with get_session() as session:
                    if not self._stats_exist(session, fid):
                        session.add(FixtureStats(**parsed_stats))

        # Step 4: standings (1 call)
        self._backfill_standings(league_id, season)

        # Step 5: odds (optional, 1 call per fixture — budget-heavy)
        if include_odds:
            self._backfill_odds(full_fixtures)

        logger.info(
            "Done league=%d season=%d. API calls remaining today: %d",
            league_id, season, calls_remaining_today()
        )

    def _backfill_standings(self, league_id: int, season: int) -> None:
        raw = self.client.get_standings(league_id, season)
        if not raw:
            return

        from datetime import datetime
        from sqlalchemy import insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        rows = []
        for entry in raw:
            team = entry.get("team", {})
            all_ = entry.get("all", {})
            goals = all_.get("goals", {})
            rows.append({
                "league_id": league_id,
                "season": season,
                "team_id": team.get("id"),
                "team_name": team.get("name", ""),
                "rank": entry.get("rank"),
                "points": entry.get("points"),
                "played": all_.get("played"),
                "won": all_.get("win"),
                "drawn": all_.get("draw"),
                "lost": all_.get("lose"),
                "goals_for": goals.get("for"),
                "goals_against": goals.get("against"),
                "goal_diff": entry.get("goalsDiff"),
                "fetched_at": datetime.utcnow(),
            })

        if not rows:
            return

        with get_session() as session:
            stmt = sqlite_insert(Standing).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["league_id", "season", "team_id"],
                set_={
                    "team_name": stmt.excluded.team_name,
                    "rank": stmt.excluded.rank,
                    "points": stmt.excluded.points,
                    "played": stmt.excluded.played,
                    "won": stmt.excluded.won,
                    "drawn": stmt.excluded.drawn,
                    "lost": stmt.excluded.lost,
                    "goals_for": stmt.excluded.goals_for,
                    "goals_against": stmt.excluded.goals_against,
                    "goal_diff": stmt.excluded.goal_diff,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)

    def _backfill_odds(self, full_fixtures: list[dict]) -> None:
        # Use actual fixtures from batch response, not the original ID list
        # (batch fetch may return fewer fixtures due to API filtering)
        fixture_ids = [r.get("fixture", {}).get("id") for r in full_fixtures if r.get("fixture", {}).get("id")]
        for fid in fixture_ids:
            raw = self.client.get_odds(fixture_id=fid, bet_type="h2h")
            if not raw:
                continue
            for bm in raw:
                bookmaker_name = bm.get("bookmakers", [{}])[0].get("name", "unknown")
                bets = bm.get("bookmakers", [{}])[0].get("bets", [])
                for bet in bets:
                    if bet.get("name") != "Match Winner":
                        continue
                    values = {v["value"]: float(v["odd"]) for v in bet.get("values", [])}
                    with get_session() as session:
                        session.merge(FixtureOdds(
                            fixture_id=fid,
                            bookmaker=bookmaker_name,
                            bet_type="h2h",
                            odd_home=values.get("Home"),
                            odd_draw=values.get("Draw"),
                            odd_away=values.get("Away"),
                        ))

    def run_all(
        self,
        league_ids: list[int] | None = None,
        seasons: list[int] | None = None,
        include_odds: bool = False,
    ) -> None:
        """Run full backfill. Stops cleanly if daily budget is low."""
        from config.leagues import ALL_LEAGUE_IDS
        target_leagues = league_ids or ALL_LEAGUE_IDS
        target_seasons = seasons or BACKFILL_SEASONS

        init_db()

        for league_id in target_leagues:
            for season in target_seasons:
                if calls_remaining_today() < 100:
                    logger.warning("Stopping backfill — budget low.")
                    return
                try:
                    self.backfill_league_season(league_id, season, include_odds=include_odds)
                except Exception as e:
                    logger.error("Error on league=%d season=%d: %s", league_id, season, e)
                    continue

    def backfill_events_for_existing(self, league_id: int, season: int) -> None:
        """Backfill events for fixtures that exist but have no events."""
        logger.info("Backfilling events for league=%d season=%d", league_id, season)
        
        if calls_remaining_today() < 50:
            logger.warning("Low on API calls. Stopping.")
            return
        
        # Get fixtures without events
        from sqlalchemy import select
        with get_session() as session:
            fixtures_without_events = session.execute(
                select(Fixture.id)
                .where(Fixture.league_id == league_id)
                .where(Fixture.season == season)
                .where(Fixture.goals_home != None)  # Only finished matches
            ).scalars().all()
            
            # Filter to those without events
            fixture_ids = []
            for fid in fixtures_without_events:
                if not self._events_exist(session, fid):
                    fixture_ids.append(fid)
        
        if not fixture_ids:
            logger.info("All fixtures already have events for league=%d season=%d", league_id, season)
            return
        
        logger.info("Found %d fixtures without events", len(fixture_ids))
        
        # Batch fetch events
        full_fixtures = self.client.get_fixtures_batch(fixture_ids)
        
        # Insert events
        with get_session() as session:
            for raw in full_fixtures:
                fid = raw.get("fixture", {}).get("id")
                if not fid:
                    continue
                
                # Skip if events already exist
                if self._events_exist(session, fid):
                    continue
                
                for ev in raw.get("events", []):
                    session.add(FixtureEvent(
                        fixture_id=fid,
                        minute=ev.get("time", {}).get("elapsed"),
                        team_id=ev.get("team", {}).get("id"),
                        player_name=ev.get("player", {}).get("name"),
                        event_type=ev.get("type"),
                        detail=ev.get("detail"),
                    ))
        
        logger.info("Done backfilling events for league=%d season=%d", league_id, season)

    def backfill_stats_for_existing(self, league_id: int, season: int) -> None:
        """Backfill stats for fixtures that exist but have no stats.
        Uses batch fetching for efficiency."""
        logger.info("Backfilling stats for league=%d season=%d", league_id, season)
        
        if calls_remaining_today() < 50:
            logger.warning("Low on API calls. Stopping.")
            return
        
        from sqlalchemy import select
        with get_session() as session:
            fixtures_without_stats = session.execute(
                select(Fixture.id)
                .where(Fixture.league_id == league_id)
                .where(Fixture.season == season)
                .where(Fixture.goals_home != None)
            ).scalars().all()
            
            fixture_ids = []
            for fid in fixtures_without_stats:
                if not self._stats_exist(session, fid):
                    fixture_ids.append(fid)
        
        if not fixture_ids:
            logger.info("All fixtures already have stats for league=%d season=%d", league_id, season)
            return
        
        logger.info("Found %d fixtures without stats, fetching in batches", len(fixture_ids))
        
        # Use batch fetch - returns full fixture data including stats in one call per 20
        full_fixtures = self.client.get_fixtures_batch(fixture_ids)
        
        with get_session() as session:
            for raw in full_fixtures:
                fid = raw.get("fixture", {}).get("id")
                if not fid:
                    continue
                
                if self._stats_exist(session, fid):
                    continue
                
                stats_data = raw.get("statistics", [])
                if stats_data:
                    parsed_stats = self._parse_stats(fid, stats_data)
                    session.add(FixtureStats(**parsed_stats))
        
        logger.info("Done backfilling stats for league=%d season=%d", league_id, season)

    def backfill_all_missing(self, league_id: int, season: int) -> None:
        """Efficiently backfill all missing data for a league using batch fetching.
        
        Strategy:
        1. Get all fixture IDs that need any data
        2. Batch fetch full fixture data (includes events, stats)
        3. Insert missing data for all types in one pass
        
        This is more efficient than separate calls because:
        - One batch call returns fixtures, events, stats, lineups
        - Avoids multiple API calls per fixture
        """
        logger.info("Full backfill for league=%d season=%d", league_id, season)
        
        if calls_remaining_today() < 30:
            logger.warning("Low on API calls. Stopping.")
            return
        
        from sqlalchemy import select
        with get_session() as session:
            # Get all finished fixtures missing ANY data
            fixtures = session.execute(
                select(Fixture.id)
                .where(Fixture.league_id == league_id)
                .where(Fixture.season == season)
                .where(Fixture.goals_home != None)
            ).scalars().all()
            
            # Filter to those missing events OR stats
            needs_data = []
            for fid in fixtures:
                has_events = self._events_exist(session, fid)
                has_stats = self._stats_exist(session, fid)
                if not has_events or not has_stats:
                    needs_data.append(fid)
        
        if not needs_data:
            logger.info("All data complete for league=%d season=%d", league_id, season)
            return
        
        logger.info("Found %d fixtures needing data", len(needs_data))
        
        # Batch fetch ALL data (events, stats included)
        full_fixtures = self.client.get_fixtures_batch(needs_data)
        
        # Insert all missing data
        events_added = 0
        stats_added = 0
        
        with get_session() as session:
            for raw in full_fixtures:
                fid = raw.get("fixture", {}).get("id")
                if not fid:
                    continue
                
                # Add missing events
                if not self._events_exist(session, fid):
                    for ev in raw.get("events", []):
                        session.add(FixtureEvent(
                            fixture_id=fid,
                            minute=ev.get("time", {}).get("elapsed"),
                            team_id=ev.get("team", {}).get("id"),
                            player_name=ev.get("player", {}).get("name"),
                            event_type=ev.get("type"),
                            detail=ev.get("detail"),
                        ))
                        events_added += 1
                
                # Add missing stats
                if not self._stats_exist(session, fid):
                    stats_data = raw.get("statistics", [])
                    if stats_data:
                        parsed_stats = self._parse_stats(fid, stats_data)
                        session.add(FixtureStats(**parsed_stats))
                        stats_added += 1
        
        logger.info("Done: added %d events, %d stats for league=%d season=%d",
                    events_added, stats_added, league_id, season)
