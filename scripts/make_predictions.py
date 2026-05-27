#!/usr/bin/env python3
"""
scripts/make_predictions.py

Generate predictions for all upcoming fixtures (7-day window).
Predictions are always generated regardless of odds availability — the model needs them.
EV/edge/odds fields are populated only when bookmaker odds exist.

Called from cron after daily_run and odds_poll.

Usage:
    python scripts/make_predictions.py              # All fixtures in 7-day window
    python scripts/make_predictions.py --dry-run   # Preview without writing
    python scripts/make_predictions.py --fixture 12345  # Specific fixture only
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.storage.db import get_session, init_db
from src.storage.models import Fixture

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
    """Make predictions for a single fixture. Predictions are always generated for the model;
    EV/edge/odds fields are populated only when bookmaker odds are available."""
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

    predictions_made = 0

    for market in MARKETS:
        bet_type = MARKET_BET_TYPE_MAP.get(market, market)

        # Get model prediction — always required, regardless of odds availability
        model_probs = get_model_prediction(market, home_id, away_id)
        if model_probs is None:
            logger.debug(f"  {market}: model prediction unavailable (no standings data)")
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

        # Calibrate probability regardless of odds
        calibration = calibrate_prediction(market, prob)
        calibrated_prob = calibration.calibrated_prob

        # Enrich with odds if available
        odds_row = s.execute(
            select(FixtureOdds).where(
                FixtureOdds.fixture_id == fixture_id,
                FixtureOdds.bet_type == bet_type,
            )
        ).scalars().first()

        ev = None
        implied_prob = None
        edge = None
        odds_decimal = None
        bookmaker = None

        if odds_row:
            market_fields = MARKET_FIELD_MAP.get(market, {})
            odd_value = market_fields.get(predicted_outcome)
            odds_decimal = getattr(odds_row, odd_value, None) if odd_value else None
            if odds_decimal and odds_decimal > 0:
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
    """Return all upcoming and live fixtures within 7 days.
    Predictions are generated for every fixture the model can score — odds are not required."""
    from datetime import timedelta
    now = datetime.utcnow()
    cutoff = now + timedelta(days=7)

    return s.execute(
        select(Fixture.id)
        .where(Fixture.date >= now)
        .where(Fixture.date <= cutoff)
        .where(Fixture.status.in_(['NS', '1H', '2H', 'HT', 'ET']))
        .order_by(Fixture.date)
    ).scalars().all()


def main():
    parser = argparse.ArgumentParser(description="Generate predictions for all upcoming fixtures")
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing to DB')
    parser.add_argument('--fixture', type=int, help='Specific fixture ID to process')
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

    logger.info(f"Processing {len(fixture_ids)} fixtures...")

    # Load fixture objects needed by UnifiedPredictionService
    from src.storage.models import Fixture as FixtureModel
    with get_session() as s:
        fixtures = s.execute(
            select(FixtureModel).where(FixtureModel.id.in_(fixture_ids))
        ).scalars().all()

        class _Stub:
            def __init__(self, f):
                self.id = f.id
                self.home_team_id = f.home_team_id
                self.away_team_id = f.away_team_id
                self.league_id = f.league_id
                self.date = f.date
                self.status = f.status

        stubs = [_Stub(f) for f in fixtures]

    from src.prediction.unified_prediction_service import UnifiedPredictionService
    service = UnifiedPredictionService()
    predictions = service.generate_with_fixture_data(stubs)

    if args.dry_run:
        logger.info(f"[DRY RUN] Would save {len(predictions)} predictions")
        for p in predictions[:10]:
            logger.info(f"  fixture={p['fixture_id']} market={p['market']} outcome={p['outcome']} prob={p['our_prob']:.2%} odds={p.get('odds')} ev={p.get('ev')}")
    else:
        saved = service.save_predictions(predictions)
        logger.info(f"Done — {len(predictions)} predictions generated, {len(saved)} saved/updated")


if __name__ == "__main__":
    main()