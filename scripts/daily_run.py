#!/usr/bin/env python3
"""
scripts/daily_run.py

Daily automation script using unified betting modules:
1. Fetch today's fixtures + odds
2. Generate predictions (all markets)
3. Identify value bets using Shin method
4. Calculate Kelly stakes
5. Log to value_bets table

Usage:
    python scripts/daily_run.py              # Full run
    python scripts/daily_run.py --dry-run     # No betting suggestions
    python scripts/daily_run.py --leagues 39,140  # Specific leagues
    python scripts/daily_run.py --markets btts,ou25  # Specific markets
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

sys.path.insert(0, '/opt/projects/bootball')

from sqlalchemy import select

from config.leagues import ALL_LEAGUE_IDS, LEAGUES
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, Standing, ValueBet, Team, PredictionRecord, ModelVersion
from src.betting.predict import predict_proba
from src.betting.value_bets import find_all_market_value_bets
from src.betting.kelly import fractional_kelly
from src.betting.alerts import BettingAlerts, BetAlert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def ensure_fixture(
    s,
    fixture_id: int,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    date: datetime,
    season: int,
    team_names: dict = None
) -> bool:
    """Ensure fixture exists in DB. Returns True if inserted."""
    existing = s.execute(
        select(Fixture).where(Fixture.id == fixture_id)
    ).scalars().first()
    if existing:
        return False

    home_team = s.execute(select(Team).where(Team.id == home_team_id)).scalars().first()
    if not home_team:
        home_team = Team(id=home_team_id, name=team_names.get(home_team_id, f"Team {home_team_id}"))
        s.add(home_team)
    
    away_team = s.execute(select(Team).where(Team.id == away_team_id)).scalars().first()
    if not away_team:
        away_team = Team(id=away_team_id, name=team_names.get(away_team_id, f"Team {away_team_id}"))
        s.add(away_team)

    s.add(Fixture(
        id=fixture_id,
        league_id=league_id,
        season=season,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        date=date,
        status="NS",
    ))
    return True


class DailyPipeline:
    def __init__(
        self,
        dry_run: bool = False,
        league_ids: list[int] | None = None,
        markets: list[str] | None = None,
        send_alerts: bool = True,
        alert_min_ev: float = 5.0,
    ):
        self.client = APIFootballClient()
        self.dry_run = dry_run
        self.league_ids = league_ids or ALL_LEAGUE_IDS
        self.markets = markets or ["h2h", "btts", "ou25", "ou15"]
        self.value_bets_found = 0
        self.send_alerts = send_alerts
        self.alert_min_ev = alert_min_ev

        # Initialize alerts
        if send_alerts:
            self.alerts = BettingAlerts(
                channels=["discord"],
                min_ev=alert_min_ev,
                min_odds=1.5,
                min_kelly=0.03,
            )
        else:
            self.alerts = None

    def _preload_models(self):
        """Pre-fit Bayesian models so they're cached for predictions."""
        import time
        from src.models.dixon_coles import BayesianDixonColesModel

        logger.info("  Pre-loading Bayesian models...")
        t0 = time.time()
        model = BayesianDixonColesModel(n_simulations=200)
        model.fit()
        logger.info(f"  Bayesian models loaded in {time.time()-t0:.1f}s")

    def run(self):
        """Main pipeline.
        
        Workflow:
        1. Fetch past 7 days COMPLETED fixtures (status=FT) - for settling predictions
        2. Fetch next 7 days UPCOMING fixtures (status=NS) - for new predictions  
        3. Generate predictions for upcoming
        4. Find value bets
        """
        logger.info("Daily pipeline starting")
        logger.info(f"  Leagues: {self.league_ids}")
        logger.info(f"  Markets: {self.markets}")
        logger.info(f"  API calls remaining: {calls_remaining_today()}")

        self._preload_models()

        now = datetime.now(ZoneInfo("UTC"))
        current_season = now.year if now.month >= 7 else now.year - 1

        # STEP 1: Fetch PAST 7 days completed fixtures for settling predictions
        # This should run multiple times/day (cron every 2 hours) to catch results
        logger.info("[STEP 1] Fetching completed fixtures for settling...")
        past_fixtures = []
        for league_id in self.league_ids:
            try:
                from_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
                to_date = now.strftime("%Y-%m-%d")
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=current_season,
                    from_date=from_date,
                    to_date=to_date,
                    status="FT",
                )
                if raw:
                    past_fixtures.extend(raw)
                    logger.info(f"  {LEAGUES.get(league_id, {}).get('name', league_id)}: {len(raw)} completed")
            except Exception as e:
                logger.warning(f"  Error fetching completed for {league_id}: {e}")
        
        # Save completed fixtures - this updates status to FT and scores
        for raw in past_fixtures:
            self._save_completed_fixture(raw, current_season)
        
        logger.info(f"  Updated {len(past_fixtures)} completed fixtures")

        # STEP 2: Fetch UPCOMING fixtures for new predictions
        logger.info("[STEP 2] Fetching upcoming fixtures...")
        all_fixtures = []
        for league_id in self.league_ids:
            try:
                from_date = now.strftime("%Y-%m-%d")
                to_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=current_season,
                    from_date=from_date,
                    to_date=to_date,
                    status="NS",
                )
                if raw:
                    all_fixtures.extend(raw)
                    logger.info(f"  {LEAGUES.get(league_id, {}).get('name', league_id)}: {len(raw)} upcoming")
            except Exception as e:
                logger.warning(f"  Error fetching upcoming for {league_id}: {e}")

        if not all_fixtures:
            logger.info("No upcoming fixtures today")
            return

        logger.info(f"Total: {len(all_fixtures)} upcoming fixtures to process")

        # STEP 3: Generate predictions and find value bets
        for raw in all_fixtures:
            self._process_fixture(raw, current_season)

        logger.info(f"Done! Found {self.value_bets_found} value bet opportunities")

    def _save_completed_fixture(self, raw: dict, season: int):
        """Save/update a completed fixture (status=FT) with final score."""
        fixture = raw.get("fixture", {})
        teams = raw.get("teams", {})
        goals = raw.get("goals", {})
        score = raw.get("score", {})
        ht = score.get("halftime", {})

        fixture_id = fixture.get("id")
        if not fixture_id:
            return

        home_goals = goals.get("home")
        away_goals = goals.get("away")
        
        with get_session() as s:
            existing = s.execute(
                select(Fixture).where(Fixture.id == fixture_id)
            ).scalar_one_or_none()
            
            if existing:
                # Update if score changed (match completed)
                if existing.status != "FT" and home_goals is not None:
                    existing.status = "FT"
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    existing.ht_goals_home = ht.get("home")
                    existing.ht_goals_away = ht.get("away")
                    # Derive outcome
                    if home_goals > away_goals:
                        existing.outcome = "H"
                    elif home_goals < away_goals:
                        existing.outcome = "A"
                    else:
                        existing.outcome = "D"
            else:
                # Create new fixture entry
                s.add(Fixture(
                    id=fixture_id,
                    league_id=raw.get("league", {}).get("id"),
                    season=season,
                    home_team_id=teams.get("home", {}).get("id"),
                    away_team_id=teams.get("away", {}).get("id"),
                    date=datetime.fromisoformat(fixture.get("date").replace("Z", "+00:00")) if fixture.get("date") else None,
                    status="FT",
                    goals_home=home_goals,
                    goals_away=away_goals,
                    ht_goals_home=ht.get("home"),
                    ht_goals_away=ht.get("away"),
                ))

    def _process_fixture(self, raw: dict, season: int):
        """Process single fixture for all markets."""
        fixture = raw.get("fixture", {})
        teams = raw.get("teams", {})

        fixture_id = fixture.get("id")
        league_id = raw.get("league", {}).get("id")
        home_team_id = teams.get("home", {}).get("id")
        away_team_id = teams.get("away", {}).get("id")
        home_name = teams.get("home", {}).get("name", "Home")
        away_name = teams.get("away", {}).get("name", "Away")

        if not all([fixture_id, league_id, home_team_id, away_team_id]):
            return

        date_utc_str = fixture.get("date")
        if date_utc_str:
            if isinstance(date_utc_str, str):
                date_utc = datetime.fromisoformat(date_utc_str.replace('Z', '+00:00'))
            else:
                date_utc = date_utc_str
        else:
            date_utc = datetime.utcnow()
        
        team_names = {
            home_team_id: home_name,
            away_team_id: away_name,
        }

        with get_session() as s:
            ensure_fixture(s, fixture_id, league_id, home_team_id, away_team_id, date_utc, season, team_names)

        logger.info(f"  {home_name} vs {away_name}:")

        for market in self.markets:
            self._find_value_bets_for_market(
                raw, fixture_id, league_id, home_team_id, away_team_id,
                home_name, away_name, market
            )

    def _find_value_bets_for_market(
        self,
        raw: dict,
        fixture_id: int,
        league_id: int,
        home_id: int,
        away_id: int,
        home_name: str,
        away_name: str,
        market: str,
    ):
        """Find value bets for a specific market."""
        try:
            model_probs = predict_proba(market, home_id, away_id)
        except Exception as e:
            logger.warning(f"    {market}: prediction failed - {e}")
            return

        outcome_str = ", ".join(f"{k}={v:.0%}" for k, v in model_probs.items())
        logger.info(f"    {market}: {outcome_str}")

        if not self.dry_run:
            best_outcome = max(model_probs.items(), key=lambda x: x[1])
            predicted_outcome = best_outcome[0]
            prob = best_outcome[1]
            
            sweet_spot = False
            if market == "btts" and best_outcome[0] == "Yes":
                pass
            elif market in ("ou25", "ou15") and best_outcome[0] == "Over":
                sweet_spot = True
            
            with get_session() as ps:
                existing_pred = ps.execute(
                    select(PredictionRecord).where(
                        PredictionRecord.fixture_id == fixture_id,
                        PredictionRecord.market == market,
                    )
                ).scalars().first()
                
                if not existing_pred:
                    active_version = ps.execute(
                        select(ModelVersion).where(
                            ModelVersion.market == market,
                            ModelVersion.is_active == True
                        )
                    ).scalar_one_or_none()
                    model_version_id = active_version.id if active_version else None
                    ps.add(PredictionRecord(
                        fixture_id=fixture_id,
                        market=market,
                        model_version_id=model_version_id,
                        model_name="ensemble",
                        predicted_outcome=predicted_outcome,
                        our_prob=prob,
                        sweet_spot=sweet_spot,
                    ))
                    ps.commit()

        with get_session() as s:
            odds_row = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fixture_id,
                    FixtureOdds.bet_type == market,
                )
            ).scalars().first()

            if not odds_row:
                return

            try:
                candidates = find_all_market_value_bets(
                    fixture_id=fixture_id,
                    home_id=home_id,
                    away_id=away_id,
                    odds_row=odds_row,
                    markets=[market],
                    ev_threshold=0.02,
                )
            except Exception as e:
                logger.warning(f"    {market}: value bet detection failed - {e}")
                return

            for candidate in candidates:
                self.value_bets_found += 1

                stake = candidate.kelly_fraction * 1000
                logger.info(
                    f"    *** VALUE BET: {market} {candidate.outcome} @ {candidate.decimal_odd:.2f} | "
                    f"EV={candidate.ev*100:.1f}% | Kelly={candidate.kelly_fraction:.1%} | "
                    f"Stake=£{stake:.2f}"
                )

                # Send Discord alert
                if self.alerts and candidate.ev * 100 >= self.alert_min_ev:
                    fixture_date = raw.get("fixture", {}).get("date", "")
                    if fixture_date:
                        fixture_date = fixture_date.replace("T", " ").replace("Z", "")[:16]

                    self.alerts.send_bet_alert(BetAlert(
                        market=market,
                        home_team=home_name,
                        away_team=away_name,
                        outcome=candidate.outcome,
                        odds=candidate.decimal_odd,
                        ev=candidate.ev * 100,
                        kelly=candidate.kelly_fraction,
                        league=LEAGUES.get(league_id, {}).get("name"),
                        edge=(candidate.our_prob - candidate.implied_prob_shin) * 100,
                        fixture_date=fixture_date,
                    ))

                if not self.dry_run:
                    existing = s.execute(
                        select(ValueBet).where(
                            ValueBet.fixture_id == fixture_id,
                            ValueBet.market == market,
                            ValueBet.outcome == candidate.outcome,
                        )
                    ).first()

                    if not existing:
                        s.add(ValueBet(
                            fixture_id=fixture_id,
                            model_name="ensemble",
                            market=market,
                            outcome=candidate.outcome,
                            our_prob=candidate.our_prob,
                            bookmaker_odd=candidate.decimal_odd,
                            implied_prob=candidate.implied_prob_shin,
                            ev=candidate.ev,
                            kelly_fraction=candidate.kelly_fraction,
                            recommended_stake=stake,
                        ))


def main():
    parser = argparse.ArgumentParser(description="Daily prediction pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--leagues", type=str, help="Comma-separated league IDs")
    parser.add_argument("--markets", type=str, help="Comma-separated markets (h2h,btts,ou25,ou15)")
    args = parser.parse_args()

    league_ids = None
    if args.leagues:
        league_ids = [int(x) for x in args.leagues.split(",")]

    markets = None
    if args.markets:
        markets = [x.strip() for x in args.markets.split(",")]

    init_db()

    pipeline = DailyPipeline(
        dry_run=args.dry_run,
        league_ids=league_ids,
        markets=markets,
    )
    pipeline.run()


if __name__ == "__main__":
    main()
