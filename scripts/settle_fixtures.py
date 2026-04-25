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
from src.storage.models import PlacedBet
from src.betting.round_manager import get_active_round_id
from sqlalchemy import select, func

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

MIN_OPEN_BETS = 5


def check_and_place_bets():
    """Check if open bets < 5 and trigger auto_bet if needed.

    Reads active round_id from file (set by get_active_round_id earlier in main).
    """
    from src.betting.round_manager import get_active_round_id

    with get_session() as s:
        round_id = get_active_round_id(s, create=False)

    if not round_id:
        logger.info("No active round, skipping auto-bet")
        return 0

    with get_session() as s:
        open_bets = s.execute(
            select(func.count()).select_from(PlacedBet)
            .where(PlacedBet.round_id == round_id)
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

    # Get/create active round (writes to file for auto_bet to read)
    with get_session() as s:
        get_active_round_id(s)

    updated = fetch_and_update_fixtures(days=args.days)
    logger.info(f"Updated {updated} fixtures")

    # Update live game statuses
    from src.settlement import update_live_fixture_statuses
    live_updated = update_live_fixture_statuses()
    logger.info(f"Updated {live_updated} live fixtures")

    result = settle_all()
    print(f"Settled: {result['bets_settled']} bets, {result['predictions_settled']} predictions, {result['value_bets_settled']} value bets, P/L: {result['bets_pnl']:+.2f}")

    if not args.no_auto_bet and not args.dry_run:
        check_and_place_bets()

    return {"fixtures_updated": updated}


if __name__ == '__main__':
    main()
