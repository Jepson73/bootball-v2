#!/usr/bin/env python3
"""
scripts/settle_fixtures.py

Settlement script:
1. Fetches completed fixtures from API-Football
2. Updates fixture statuses and scores in DB
3. Settles pending PlacedBets (with Discord alerts)
4. If open bets < 5, triggers auto_bet to place more

Usage:
    python scripts/settle_fixtures.py         # Run
    python scripts/settle_fixtures.py --dry-run  # Preview without changes
    python scripts/settle_fixtures.py --days 7  # Fetch last 7 days
    python scripts/settle_fixtures.py --no-auto-bet  # Skip auto-bet trigger
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/projects/bootball')

from src.settlement import settle_all, fetch_and_update_fixtures
from src.storage.db import init_db, get_session
from src.storage.models import PlacedBet, BankrollRound
from sqlalchemy import select, func

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

MIN_OPEN_BETS = 5


def check_and_place_bets():
    """Check if open bets < 5 and trigger auto_bet if needed."""
    with get_session() as s:
        active_round = s.execute(
            select(BankrollRound).where(BankrollRound.is_active == True)
        ).scalar_one_or_none()

        if not active_round:
            logger.info("No active bankroll round, skipping auto-bet")
            return 0

        open_bets = s.execute(
            select(func.count()).select_from(PlacedBet)
            .where(PlacedBet.round_id == active_round.id)
            .where(PlacedBet.settled == False)
        ).scalar() or 0

        logger.info(f"Open bets: {open_bets}")

        if open_bets >= MIN_OPEN_BETS:
            logger.info(f"Open bets ({open_bets}) >= {MIN_OPEN_BETS}, skipping auto-bet")
            return 0

        logger.info(f"Open bets ({open_bets}) < {MIN_OPEN_BETS}, triggering auto-bet...")

    import subprocess
    result = subprocess.run(
        [sys.executable, 'scripts/auto_bet.py', '--bet-only'],
        capture_output=True, text=True, timeout=120
    )
    logger.info(f"Auto-bet output: {result.stdout[:500]}")
    if result.stderr:
        logger.warning(f"Auto-bet stderr: {result.stderr[:200]}")
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--no-auto-bet', action='store_true', help='Skip auto-bet trigger')
    args = parser.parse_args()

    init_db()

    updated = fetch_and_update_fixtures(days=args.days)
    logger.info(f"Updated {updated} fixtures")

    result = settle_all()
    print(f"Settled: {result['bets_settled']}, P/L: {result['total_pnl']:+.2f}")

    if not args.no_auto_bet and not args.dry_run:
        check_and_place_bets()


if __name__ == '__main__':
    main()
