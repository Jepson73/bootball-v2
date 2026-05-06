#!/usr/bin/env python3
"""
scripts/daily_run.py

Pure orchestrator pipeline - produces facts, emits events, NO alerting.

Responsibilities:
1. Fetch fixtures + odds
2. Generate predictions
3. Compute value bets
4. Persist results
5. Emit structured events via EventBus

DO NOT:
- Send Discord messages
- Build alert strings  
- Call alerting libraries directly
- Format dashboard summaries
- Duplicate notification logic

ALL side effects are handled by EventBus Consumers.
"""

import argparse
import logging
import sys
from pathlib import Path
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from config.leagues import ALL_LEAGUE_IDS, LEAGUES
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, Team, ModelVersion

from src.betting.ev import expected_value
from src.betting.kelly import fractional_kelly
from src.betting.shin import shin_probabilities
from src.betting.prediction import get_model_prediction
from src.models.calibrator import calibrate_prediction

from src.alerts.event_bus import event_bus as EventBus, Events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EV_THRESHOLD = 0.05
KELLY_FRACTION = 0.25


def run_with_context(run_id: str, mode: str = "daily", dry_run: bool = False):
    """
    Execute pipeline with run context for tracking.
    Returns (success, error_count, duration_seconds)
    """
    from backend.experiment_tracker import ExperimentTracker
    
    start_time = time.time()
    errors = []
    
    try:
        EventBus.emit(
            Events.RUN_STARTED,
            {
                "run_id": run_id,
                "mode": mode,
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        pipeline = DailyPipeline(
            dry_run=dry_run,
            context={"run_id": run_id, "mode": mode},
        )
        pipeline.run()
        
        duration = time.time() - start_time
        
        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": mode,
                "total_bets": len(pipeline.value_bets),
                "total_ev": sum(b.get("ev", 0) for b in pipeline.value_bets),
                "errors": errors,
                "duration": duration,
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        return True, len(errors), duration
        
    except Exception as e:
        errors.append(str(e))
        duration = time.time() - start_time
        
        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": mode,
                "total_bets": 0,
                "total_ev": 0,
                "errors": errors,
                "duration": duration,
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        return False, len(errors), duration


class DailyPipeline:
    """
    Pure computation pipeline.
    No alerts, no formatting, no messaging.
    Emits events only.
    """

    def __init__(self, dry_run=False, league_ids=None, markets=None, catchup_days=0, context=None):
        self.client = APIFootballClient()
        self.dry_run = dry_run
        self.league_ids = league_ids or ALL_LEAGUE_IDS
        self.markets = markets or ["h2h", "btts", "ou25", "ou15"]
        self.catchup_days = catchup_days
        self.context = context or {}

        self.value_bets = []
        self.errors = []
        self.prediction_count = 0
        self.fixture_count = 0

    def run(self):
        logger.info("Daily pipeline starting")
        logger.info(f"Leagues: {len(self.league_ids)} markets: {self.markets}")

        now = datetime.now(ZoneInfo("UTC"))
        season = now.year if now.month >= 7 else now.year - 1

        run_id = self.context.get("run_id")

        EventBus.emit(
            Events.RUN_STARTED,
            {
                "run_id": run_id,
                "mode": "daily",
                "timestamp": now.isoformat(),
            },
        )

        self._fetch_completed(now, season)

        fixtures = self._fetch_upcoming(now, season)
        self.fixture_count = len(fixtures)

        for raw in fixtures:
            self._process_fixture(raw, season)

        self._run_predictions()

        EventBus.emit(
            Events.PREDICTIONS_GENERATED,
            {
                "run_id": run_id,
                "fixture_count": self.fixture_count,
                "market_count": len(self.markets),
                "prediction_count": self.prediction_count,
                "timestamp": now.isoformat(),
            },
        )

        EventBus.emit(
            Events.BETS_GENERATED,
            {
                "run_id": run_id,
                "bets": [
                    {
                        "fixture_id": b["fixture_id"],
                        "market": b["market"],
                        "outcome": b["outcome"],
                        "odds": b["odds"],
                        "ev": b["ev"],
                        "stake": b["stake"],
                        "timestamp": b.get("timestamp", now.isoformat()),
                    }
                    for b in self.value_bets
                ],
            },
        )

        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": "daily",
                "total_bets": len(self.value_bets),
                "total_ev": sum(b.get("ev", 0) for b in self.value_bets),
                "errors": self.errors,
                "timestamp": now.isoformat(),
            },
        )

        logger.info(f"Pipeline complete. value_bets={len(self.value_bets)}")

    def _fetch_completed(self, now, season):
        logger.info("Fetching completed fixtures for settlement")

        days_back = max(1, self.catchup_days or 1)

        for league_id in self.league_ids:
            try:
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=season,
                    from_date=(now - timedelta(days=days_back)).strftime("%Y-%m-%d"),
                    to_date=now.strftime("%Y-%m-%d"),
                    status="FT",
                )
                if raw:
                    settled_count = self._save_completed(raw, season)
                    if settled_count > 0:
                        run_id = self.context.get("run_id")
                        EventBus.emit(
                            Events.BET_SETTLED,
                            {
                                "run_id": run_id,
                                "settled_count": settled_count,
                                "pnl_total": 0,
                                "wins": 0,
                                "losses": 0,
                                "timestamp": now.isoformat(),
                            },
                        )
            except Exception as e:
                self.errors.append(str(e))

    def _save_completed(self, raw, season):
        count = 0
        for match in raw:
            fixture = match["fixture"]
            fid = fixture["id"]

            with get_session() as s:
                existing = s.execute(
                    select(Fixture).where(Fixture.id == fid)
                ).scalar_one_or_none()

                if not existing:
                    continue

                goals = fixture.get("goals", {})
                existing.goals_home = goals.get("home")
                existing.goals_away = goals.get("away")
                existing.status = "FT"

                if goals.get("home") is not None and goals.get("away") is not None:
                    if goals["home"] > goals["away"]:
                        existing.outcome = "H"
                    elif goals["home"] < goals["away"]:
                        existing.outcome = "A"
                    else:
                        existing.outcome = "D"

                s.commit()
                count += 1

        return count

    def _fetch_upcoming(self, now, season):
        logger.info("Fetching upcoming fixtures")

        fixtures = []

        for league_id in self.league_ids:
            try:
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=season,
                    from_date=now.strftime("%Y-%m-%d"),
                    to_date=(now + timedelta(days=7)).strftime("%Y-%m-%d"),
                    status="NS",
                )
                if raw:
                    fixtures.extend(raw)
            except Exception as e:
                self.errors.append(str(e))

        return fixtures

    def _process_fixture(self, raw, season):
        fixture = raw["fixture"]
        teams = raw["teams"]

        fixture_id = fixture["id"]

        with get_session() as s:
            self._ensure_fixture(s, raw, season)
            self._fetch_odds(s, fixture_id)

    def _run_predictions(self):
        from scripts.make_predictions import make_predictions_for_fixture

        with get_session() as s:
            fixtures = s.execute(
                select(Fixture.id).where(Fixture.status.in_(["NS", "1H", "2H", "HT"]))
            ).scalars().all()

        for fid in fixtures:
            with get_session() as s:
                make_predictions_for_fixture(s, fid, self.dry_run, context=self.context)
                self.prediction_count += 1

    def _ensure_fixture(self, s, raw, season):
        fixture = raw["fixture"]
        teams = raw["teams"]

        fid = fixture["id"]

        if s.execute(select(Fixture).where(Fixture.id == fid)).scalar():
            return

        s.add(
            Fixture(
                id=fid,
                league_id=raw["league"]["id"],
                season=season,
                home_team_id=teams["home"]["id"],
                away_team_id=teams["away"]["id"],
                date=datetime.fromisoformat(fixture["date"].replace("Z", "+00:00")),
                status="NS",
            )
        )

    def _fetch_odds(self, s, fixture_id):
        try:
            odds = self.client.get_odds(fixture_id=fixture_id)
        except Exception:
            return

        existing = s.execute(
            select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_id)
        ).scalar_one_or_none()

        if not existing:
            odds_data = odds.get("bookmakers", [])
            if odds_data:
                bookmaker = odds_data[0]
                b = bookmaker.get("bets", [])
                for bet in b:
                    bet_type = bet["name"]
                    if "Home" in bet_type:
                        o_home = bet["values"][0]["odd"] if len(bet["values"]) > 0 else None
                        o_draw = bet["values"][1]["odd"] if len(bet["values"]) > 1 else None
                        o_away = bet["values"][2]["odd"] if len(bet["values"]) > 2 else None
                        s.add(
                            FixtureOdds(
                                fixture_id=fixture_id,
                                bookmaker=bookmaker["name"],
                                bet_type=bet_type,
                                odd_home=float(o_home) if o_home else None,
                                odd_draw=float(o_draw) if o_draw else None,
                                odd_away=float(o_away) if o_away else None,
                            )
                        )
        else:
            odds_data = odds.get("bookmakers", [])
            if odds_data:
                bookmaker = odds_data[0]
                b = bookmaker.get("bets", [])
                for bet in b:
                    bet_type = bet["name"]
                    if "Home" in bet_type:
                        o_home = bet["values"][0]["odd"] if len(bet["values"]) > 0 else None
                        o_draw = bet["values"][1]["odd"] if len(bet["values"]) > 1 else None
                        o_away = bet["values"][2]["odd"] if len(bet["values"]) > 2 else None
                        if o_home:
                            existing.odd_home = float(o_home)
                        if o_draw:
                            existing.odd_draw = float(o_draw)
                        if o_away:
                            existing.odd_away = float(o_away)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--leagues", type=str)
    parser.add_argument("--markets", type=str)
    parser.add_argument("--catchup", type=int, default=0)
    args = parser.parse_args()

    init_db()

    league_ids = list(map(int, args.leagues.split(","))) if args.leagues else None
    markets = args.markets.split(",") if args.markets else None

    pipeline = DailyPipeline(
        dry_run=args.dry_run,
        league_ids=league_ids,
        markets=markets,
        catchup_days=args.catchup,
    )

    pipeline.run()


if __name__ == "__main__":
    main()