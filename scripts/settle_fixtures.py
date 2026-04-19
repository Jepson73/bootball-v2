#!/usr/bin/env python3
"""
scripts/settle_fixtures.py

Runs every 2 hours to:
1. Fetch completed fixtures (status=FT) - update goals/scores
2. Settle PlacedBets for completed matches
3. Update existing fixture odds

Usage:
    python scripts/settle_fixtures.py         # Run (no dry-run)
    python scripts/settle_fixtures.py --dry-run  # Preview without changes
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/projects/bootball')

from sqlalchemy import select

from config.leagues import ALL_LEAGUE_IDS
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, PlacedBet, BankrollRound, Team, League, PredictionRecord
from src.betting.predict import predict_proba
from src.betting.alerts import send_bet_placed_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def get_market_result(fixture: Fixture, market: str) -> str | None:
    """Determine actual result from fixture goals."""
    if fixture.goals_home is None or fixture.goals_away is None:
        return None
    
    if market == "h2h":
        if fixture.goals_home > fixture.goals_away:
            return "1"
        elif fixture.goals_home < fixture.goals_away:
            return "2"
        return "X"
    
    total = fixture.goals_home + fixture.goals_away
    
    if market == "btts":
        return "Yes" if (fixture.goals_home > 0 and fixture.goals_away > 0) else "No"
    elif market == "ou25":
        return "Over" if total > 2.5 else "Under"
    elif market == "ou15":
        return "Over" if total > 1.5 else "Under"
    
    return None


def calculate_pnl(outcome: str, result: str, stake: float, odds: float) -> float:
    """Calculate P/L for a bet."""
    if outcome == result:
        return (odds - 1) * stake
    return -stake


def settle_fixtures(dry_run: bool = False, days: int = 7):
    """Fetch completed fixtures and settle bets."""
    init_db()
    
    client = APIFootballClient()
    team_cache = {}
    league_cache = {}
    
    with get_session() as s:
        for league_id in ALL_LEAGUE_IDS:
            league = s.execute(select(League).where(League.id == league_id)).scalar_one_or_none()
            league_cache[league_id] = league.name if league else f"League {league_id}"
        
        teams = s.execute(select(Team)).scalars().all()
        for t in teams:
            team_cache[t.id] = t.name
    
    from datetime import date as date_type
    cutoff = datetime.utcnow() - timedelta(days=days)
    today_str = date_type.today().strftime("%Y-%m-%d")
    current_year = date_type.today().year
    
    logger.info(f"Fetching completed fixtures (past {days} days)")
    
    # Fetch per league
    completed = []
    for lid in ALL_LEAGUE_IDS[:20]:  # Limit API calls
        season = current_year if lid in [1602, 253, 98, 176, 113, 1191] else current_year - 1
        try:
            results = client.get_fixtures(
                league_id=lid,
                season=season,
                from_date=cutoff.strftime("%Y-%m-%d"),
                to_date=today_str,
                status="FT",
            )
            if results:
                completed.extend(results)
        except Exception as e:
            logger.warning(f"Error fetching league {lid}: {e}")
    
    logger.info(f"Got {len(completed)} completed fixtures")
    
    with get_session() as s:
        updated = 0
        settled = 0
        
        for fix_data in completed:
            fix_info = fix_data.get("fixture", {})
            fix_id = fix_info.get("id")
            if not fix_id:
                continue
            
            goals = fix_data.get("goals", {})
            home_goals = goals.get("home")
            away_goals = goals.get("away")
            
            if home_goals is None:
                continue
            
            existing = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
            
            if existing:
                if existing.status != "FT":
                    existing.status = "FT"
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    updated += 1
                    logger.info(f"Fixture {fix_id} completed: {home_goals}-{away_goals}")
                elif existing.goals_home != home_goals:
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    logger.info(f"Fixture {fix_id} score corrected: {home_goals}-{away_goals}")
        
        if updated > 0 and not dry_run:
            s.commit()
            logger.info(f"Updated {updated} fixtures")
        
        pending_bets = s.execute(
            select(PlacedBet).where(PlacedBet.settled == False)
        ).scalars().all()
        
        total_pnl = 0.0
        
        for bet in pending_bets:
            fixture = s.execute(select(Fixture).where(Fixture.id == bet.fixture_id)).scalar_one_or_none()
            
            if not fixture or fixture.status != "FT" or fixture.goals_home is None:
                continue
            
            result = get_market_result(fixture, bet.market)
            
            if result is None:
                continue
            
            bet.settled = True
            bet.result = result
            bet.pnl = calculate_pnl(bet.outcome, result, bet.stake, bet.odds)
            bet.settled_at = datetime.utcnow()
            
            if bet.outcome == result:
                bet.won = True
            else:
                bet.won = False
            
            total_pnl += bet.pnl
            settled += 1
            
            home_name = team_cache.get(fixture.home_team_id, str(fixture.home_team_id))
            away_name = team_cache.get(fixture.away_team_id, str(fixture.away_team_id))
            logger.info(f"Bet {bet.id}: {bet.market} {bet.outcome} @ {bet.odds} → {result} | P/L: {bet.pnl:+.2f}")
        
        if settled > 0 and not dry_run:
            s.commit()
            
            round_id = pending_bets[0].round_id if pending_bets else None
            if round_id:
                round_obj = s.execute(select(BankrollRound).where(BankrollRound.id == round_id)).scalar_one_or_none()
                if round_obj:
                    round_obj.total_pnl = (round_obj.total_pnl or 0) + total_pnl
                    s.commit()
            
            logger.info(f"Settled {settled} bets, P/L: {total_pnl:+.2f}")
        
        logger.info(f"Done. Updated: {updated}, Settled: {settled}, P/L: {total_pnl:+.2f}")
    
    update_predictions(dry_run=dry_run)


def update_predictions(dry_run: bool = False):
    """Generate predictions for upcoming fixtures in background."""
    logger.info("Updating predictions for upcoming fixtures...")
    
    from src.storage.models import PredictionRecord
    
    with get_session() as s:
        future = datetime.utcnow() + timedelta(hours=36)
        fixtures = s.execute(
            select(Fixture)
            .where(Fixture.date >= datetime.utcnow())
            .where(Fixture.date <= future)
            .where(Fixture.status == 'NS')
        ).scalars().all()
        
        updated = 0
        for fix in fixtures[:50]:
            for market in ['h2h', 'btts', 'ou25', 'ou15']:
                existing = s.execute(
                    select(PredictionRecord)
                    .where(PredictionRecord.fixture_id == fix.id)
                    .where(PredictionRecord.market == market)
                ).scalars().first()
                
                if existing:
                    created = getattr(existing, 'created_at', None)
                    if created and (datetime.utcnow() - created.replace(tzinfo=None)) < timedelta(hours=4):
                        continue
                    if existing and not dry_run:
                        try:
                            probs = predict_proba(market, fix.home_team_id, fix.away_team_id)
                            if probs:
                                best = max(probs.items(), key=lambda x: x[1])
                                existing.predicted_outcome = best[0]
                                existing.our_prob = best[1]
                                existing.created_at = datetime.utcnow()
                                updated += 1
                        except Exception as e:
                            logger.warning(f"Prediction error fix {fix.id} {market}: {e}")
                        continue
                
                try:
                    probs = predict_proba(market, fix.home_team_id, fix.away_team_id)
                    if not probs:
                        continue
                    best = max(probs.items(), key=lambda x: x[1])
                    
                    sweet = False
                    if market in ('btts', 'ou25', 'ou15') and best[0] in ('Yes', 'Over'):
                        sweet = True
                    
                    if not dry_run:
                        s.add(PredictionRecord(
                            fixture_id=fix.id,
                            market=market,
                            model_name='ensemble',
                            predicted_outcome=best[0],
                            our_prob=best[1],
                            sweet_spot=sweet,
                        ))
                        updated += 1
                except Exception as e:
                    logger.warning(f"Error predicting {fix.id} {market}: {e}")
        
        if updated > 0 and not dry_run:
            s.commit()
        
        logger.info(f"Generated {updated} predictions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Settle fixtures and bets")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--days", type=int, default=7, help="Days to look back")
    args = parser.parse_args()
    
    settle_fixtures(dry_run=args.dry_run, days=args.days)