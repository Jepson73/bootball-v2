#!/usr/bin/env python3
"""
scripts/make_predictions.py

Make predictions for fixtures that have odds.
Can be called from daily_run (after fixture fetch) or odds_poll (after odds update).

Usage:
    python scripts/make_predictions.py              # Run for all fixtures needing predictions
    python scripts/make_predictions.py --dry-run   # Preview without changes
    python scripts/make_predictions.py --fixture 12345  # Specific fixture only
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, ModelVersion
from src.betting.prediction import get_model_prediction
from src.betting.ev import expected_value
from src.models.calibrator import calibrate_prediction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

MARKET_BET_TYPE_MAP = {
    "h2h": "h2h",
    "btts": "btts",
    "ou25": "over_under",
    "ou15": "over_under",
}

MARKET_FIELD_MAP = {
    "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
}

MARKETS = ["h2h", "btts", "ou25", "ou15"]


def make_predictions_for_fixture(s, fixture_id: int, dry_run: bool = False, context: "RunContext | None" = None) -> int:
    """Make predictions for a single fixture if odds exist."""
    from backend.run_context import require_run_context
    from backend.execution_engine import enforce_execution_boundary
    
    enforce_execution_boundary()
    require_run_context(context, "make_predictions_for_fixture")
    
    fixture = s.execute(
        select(Fixture).where(Fixture.id == fixture_id)
    ).scalar_one_or_none()
    
    if not fixture:
        logger.warning(f"Fixture {fixture_id} not found")
        return 0
    
    home_id = fixture.home_team_id
    away_id = fixture.away_team_id
    home_name = f"Team {home_id}"  # Will be replaced if needed
    away_name = f"Team {away_id}"
    
    predictions_made = 0
    
    for market in MARKETS:
        bet_type = MARKET_BET_TYPE_MAP.get(market, market)
        
        # Check if odds exist for this market
        odds_row = s.execute(
            select(FixtureOdds).where(
                FixtureOdds.fixture_id == fixture_id,
                FixtureOdds.bet_type == bet_type,
            )
        ).scalars().first()
        
        if not odds_row:
            logger.debug(f"  {market}: No odds available")
            continue
        
        # Get model prediction
        model_probs = get_model_prediction(market, home_id, away_id)
        if model_probs is None:
            logger.warning(f"  {market}: prediction failed")
            continue
        
        # Get best outcome
        best_outcome = max(model_probs.items(), key=lambda x: x[1])
        predicted_outcome = best_outcome[0]
        prob = best_outcome[1]
        
        # Sweet spot logic
        sweet_spot = False
        if market == "btts" and best_outcome[0] == "Yes":
            sweet_spot = True
        elif market in ("ou25", "ou15") and best_outcome[0] == "Over":
            sweet_spot = True
        
        # Get odds for predicted outcome
        market_fields = MARKET_FIELD_MAP.get(market, {})
        odd_value = market_fields.get(predicted_outcome)
        odds_decimal = getattr(odds_row, odd_value, None) if odd_value else None
        
        # Calculate EV, implied prob, edge
        ev = None
        calibrated_prob = prob
        implied_prob = None
        edge = None
        bookmaker = None
        
        if odds_decimal and odds_decimal > 0:
            calibration = calibrate_prediction(market, prob)
            calibrated_prob = calibration.calibrated_prob
            ev = expected_value(calibrated_prob, odds_decimal)
            implied_prob = 1.0 / odds_decimal
            edge = (calibrated_prob - implied_prob) * 100
            bookmaker = odds_row.bookmaker
        
        # Get active model version
        active_version = s.execute(
            select(ModelVersion).where(
                ModelVersion.market == market,
                ModelVersion.is_active == True
            )
        ).scalar_one_or_none()
        model_version_id = active_version.id if active_version else None
        
        if dry_run:
            logger.info(f"  [DRY RUN] {market}: {predicted_outcome} @ {prob:.0%}, odds={odds_decimal}, ev={ev}")
            continue
        
        # Check for existing prediction
        existing_pred = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.fixture_id == fixture_id,
                PredictionRecord.market == market,
            )
        ).scalars().first()
        
        if existing_pred:
            existing_pred.odds_decimal = odds_decimal
            existing_pred.ev = ev
            existing_pred.calibrated_prob = calibrated_prob
            existing_pred.implied_prob = implied_prob
            existing_pred.edge = edge
            existing_pred.bookmaker = bookmaker
            if context:
                existing_pred.run_id = context.run_id
        else:
            s.add(PredictionRecord(
                fixture_id=fixture_id,
                market=market,
                model_version_id=model_version_id,
                model_name="lgbm",
                predicted_outcome=predicted_outcome,
                our_prob=prob,
                sweet_spot=sweet_spot,
                odds_decimal=odds_decimal,
                ev=ev,
                calibrated_prob=calibrated_prob,
                implied_prob=implied_prob,
                edge=edge,
                bookmaker=bookmaker,
                run_id=context.run_id if context else None,
            ))
        
        predictions_made += 1
        logger.info(f"  {market}: {predicted_outcome} @ {prob:.0%}, odds={odds_decimal}, ev={ev}")
    
    return predictions_made


def find_fixtures_needing_predictions(s) -> list[int]:
    """Find fixtures that need predictions (have odds but no prediction or missing odds data)."""
    now = datetime.utcnow()
    cutoff = now.replace(hour=23, minute=59, second=59)
    
    # Fixtures that are upcoming or live, and have some odds but may need predictions
    fixtures = s.execute(
        select(Fixture.id)
        .where(Fixture.date <= cutoff)
        .where(Fixture.status.in_(['NS', '1H', '2H', 'HT', 'ET']))
    ).scalars().all()
    
    result = []
    for fix_id in fixtures:
        # Check if we have odds for at least one market
        odds_count = s.execute(
            select(func.count(FixtureOdds.id))
            .where(FixtureOdds.fixture_id == fix_id)
        ).scalar() or 0
        
        if odds_count > 0:
            result.append(fix_id)
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Make predictions for fixtures with odds")
    parser.add_argument('--dry-run', action='store_true', help='Preview without changes')
    parser.add_argument('--fixture', type=int, help='Specific fixture ID to process')
    parser.add_argument('--limit', type=int, default=50, help='Max fixtures to process')
    args = parser.parse_args()
    
    init_db()
    
    with get_session() as s:
        if args.fixture:
            fixture_ids = [args.fixture]
        else:
            fixture_ids = find_fixtures_needing_predictions(s)
    
    if not fixture_ids:
        logger.info("No fixtures need predictions")
        return
    
    fixture_ids = fixture_ids[:args.limit]
    logger.info(f"Processing {len(fixture_ids)} fixtures...")
    
    total_predictions = 0
    for fix_id in fixture_ids:
        with get_session() as s:
            logger.info(f"Processing fixture {fix_id}...")
            count = make_predictions_for_fixture(s, fix_id, args.dry_run)
            if not args.dry_run:
                s.commit()
            total_predictions += count
    
    logger.info(f"Done! Made {total_predictions} predictions")


if __name__ == "__main__":
    main()