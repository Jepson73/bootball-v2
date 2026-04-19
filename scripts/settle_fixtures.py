#!/usr/bin/env python3
"""
scripts/settle_fixtures.py

Standalone script that:
1. Fetches completed fixtures from API-Football
2. Updates fixture statuses and scores in DB
3. Settles pending PlacedBets
4. Updates PredictionRecords for tracking

Usage:
    python scripts/settle_fixtures.py         # Run
    python scripts/settle_fixtures.py --dry-run  # Preview without changes
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/projects/bootball')

from src.settlement import settle_all
from src.storage.db import get_session, init_db
from src.storage.models import PredictionRecord
from src.betting.predict import predict_proba
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def update_predictions(dry_run: bool = False):
    """Generate predictions for upcoming fixtures."""
    logger.info("Updating predictions for upcoming fixtures...")

    with get_session() as s:
        future = datetime.utcnow() + timedelta(hours=36)
        fixtures = s.execute(
            select(PredictionRecord)
            .join(PredictionRecord.fixture)
            .where(PredictionRecord.fixture.has(date=datetime.utcnow()))
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
                if not dry_run:
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
                updated += 1
            except Exception as e:
                logger.warning(f"Prediction error fix {fix.id} {market}: {e}")

    logger.info(f"Generated {updated} predictions")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()

    init_db()

    result = settle_all(days=args.days)
    print(f"Updated: {result['fixtures_updated']}, Settled: {result['bets_settled']}, P/L: {result['total_pnl']:+.2f}")

    if not args.dry_run:
        update_predictions(dry_run=args.dry_run)


if __name__ == '__main__':
    main()