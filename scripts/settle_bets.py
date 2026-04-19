#!/usr/bin/env python3
"""
scripts/settle_bets.py

Settlement job: Process completed fixtures, update bet results,
and track bankroll.

Usage:
    python scripts/settle_bets.py              # Run settlement
    python scripts/settle_bets.py --dry-run    # Preview without changes
    python scripts/settle_bets.py --days 3      # Check last 3 days
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/projects/bootball')

from sqlalchemy import select

from src.storage.db import get_session, init_db
from src.storage.models import Fixture, ValueBet, SettledBet, Bankroll

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

INITIAL_BANKROLL = 1000.0


def get_market_result(fixture: Fixture, market: str) -> str | None:
    """
    Determine the actual result for a market from fixture goals.

    Args:
        fixture: Fixture ORM object
        market: h2h, btts, ou25, ou15

    Returns:
        Outcome string or None if unable to determine
    """
    if fixture.goals_home is None or fixture.goals_away is None:
        return None

    if market == "h2h":
        if fixture.goals_home > fixture.goals_away:
            return "1"
        elif fixture.goals_home < fixture.goals_away:
            return "2"
        else:
            return "X"

    elif market == "btts":
        home_scored = fixture.goals_home > 0
        away_scored = fixture.goals_away > 0
        if home_scored and away_scored:
            return "Yes"
        else:
            return "No"

    elif market == "ou25":
        total = (fixture.goals_home or 0) + (fixture.goals_away or 0)
        return "Over" if total > 2.5 else "Under"

    elif market == "ou15":
        total = (fixture.goals_home or 0) + (fixture.goals_away or 0)
        return "Over" if total > 1.5 else "Under"

    return None


def calculate_pnl(outcome: str, result: str, stake: float, odds: float) -> float:
    """
    Calculate profit/loss for a bet.

    Args:
        outcome: Our predicted outcome (e.g., "Yes", "Over", "1")
        result: Actual outcome from fixture
        stake: Amount staked
        odds: Decimal odds

    Returns:
        P/L in units (negative for loss)
    """
    if outcome == result:
        return (odds - 1) * stake
    return -stake


def get_current_bankroll(s) -> float:
    """Get current bankroll balance."""
    latest = s.execute(
        select(Bankroll).order_by(Bankroll.date.desc()).limit(1)
    ).scalars().first()

    if latest:
        return latest.balance

    latest_settled = s.execute(
        select(SettledBet).order_by(SettledBet.settled_at.desc()).limit(1)
    ).scalars().first()

    if latest_settled:
        return INITIAL_BANKROLL + sum(
            b.pnl for b in s.execute(select(SettledBet)).scalars().all()
        )

    return INITIAL_BANKROLL


def settle_bets(dry_run: bool = True, days: int = 7):
    """Settle unsettled value bets for completed fixtures."""
    init_db()

    cutoff = datetime.utcnow() - timedelta(days=days)

    with get_session() as s:
        unsettled = s.execute(
            select(ValueBet).where(ValueBet.settled == False).join(Fixture)
        ).scalars().all()

        if not unsettled:
            logger.info("No unsettled bets found")
            return

        logger.info(f"Found {len(unsettled)} unsettled bets")

        settled_count = 0
        total_pnl = 0.0
        current_bankroll = get_current_bankroll(s) if not dry_run else INITIAL_BANKROLL

        for bet in unsettled:
            fixture = s.execute(
                select(Fixture).where(Fixture.id == bet.fixture_id)
            ).scalars().first()

            if not fixture:
                logger.warning(f"Fixture {bet.fixture_id} not found, skipping")
                continue

            if fixture.status not in ("FT", "AET", "PEN"):
                continue

            if fixture.date and fixture.date > cutoff:
                continue

            actual_result = get_market_result(fixture, bet.market)

            if actual_result is None:
                logger.warning(f"Could not determine result for fixture {bet.fixture_id}")
                continue

            won = bet.outcome == actual_result
            stake = bet.recommended_stake or 1.0
            pnl = calculate_pnl(bet.outcome, actual_result, stake, bet.bookmaker_odd)

            bet.settled = True
            bet.result = actual_result
            bet.won = won
            bet.pnl = pnl

            if not dry_run:
                settled_record = SettledBet(
                    fixture_id=bet.fixture_id,
                    market=bet.market,
                    outcome=bet.outcome,
                    stake=stake,
                    odds=bet.bookmaker_odd,
                    our_prob=bet.our_prob,
                    result=actual_result,
                    won=won,
                    pnl=pnl,
                )
                s.add(settled_record)

            total_pnl += pnl
            settled_count += 1

            result_str = "WON" if won else "LOST"
            logger.info(
                f"  {fixture.home_team_id} vs {fixture.away_team_id}: "
                f"{bet.market} {bet.outcome} → {actual_result} | "
                f"{result_str} | P/L: {pnl:+.2f}"
            )

        if not dry_run:
            new_balance = current_bankroll + total_pnl
            bankroll_record = Bankroll(
                date=datetime.utcnow(),
                balance=new_balance,
                total_staked=sum(bet.recommended_stake or 1.0 for _ in range(settled_count)),
                total_won=total_pnl if total_pnl > 0 else 0,
                total_lost=abs(total_pnl) if total_pnl < 0 else 0,
                bet_count=settled_count,
                win_count=sum(1 for b in unsettled if b.settled and b.won),
                notes=f"Settled {settled_count} bets",
            )
            s.add(bankroll_record)
            logger.info(f"Bankroll updated: {current_bankroll:.2f} → {new_balance:.2f}")

        logger.info(
            f"Settlement complete: {settled_count} bets | "
            f"Total P/L: {total_pnl:+.2f} | "
            f"New balance: {current_bankroll + total_pnl:.2f}"
        )


def show_bankroll():
    """Display current bankroll status."""
    with get_session() as s:
        latest = s.execute(
            select(Bankroll).order_by(Bankroll.date.desc()).limit(1)
        ).scalars().first()

        if latest:
            logger.info(f"Current bankroll: {latest.balance:.2f}")
            logger.info(f"Total bets: {latest.bet_count}, Wins: {latest.win_count}")
            logger.info(f"Total staked: {latest.total_staked:.2f}")
            logger.info(f"Last updated: {latest.date}")
        else:
            logger.info(f"No bankroll records. Starting balance: {INITIAL_BANKROLL:.2f}")


def show_results(days: int = 30):
    """Show recent betting results."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    with get_session() as s:
        settled = s.execute(
            select(SettledBet).where(SettledBet.settled_at >= cutoff)
        ).scalars().all()

        if not settled:
            logger.info(f"No settled bets in last {days} days")
            return

        total_pnl = sum(b.pnl for b in settled)
        win_count = sum(1 for b in settled if b.won)
        total_staked = sum(b.stake for b in settled)

        logger.info(f"=== Results (Last {days} days) ===")
        logger.info(f"Total bets: {len(settled)}")
        logger.info(f"Wins: {win_count} ({win_count/len(settled)*100:.1f}%)")
        logger.info(f"Total staked: {total_staked:.2f}")
        logger.info(f"Total P/L: {total_pnl:+.2f}")
        if total_staked > 0:
            logger.info(f"ROI: {total_pnl/total_staked*100:+.2f}%")

        by_market = {}
        for bet in settled:
            if bet.market not in by_market:
                by_market[bet.market] = {"count": 0, "wins": 0, "pnl": 0}
            by_market[bet.market]["count"] += 1
            by_market[bet.market]["wins"] += 1 if bet.won else 0
            by_market[bet.market]["pnl"] += bet.pnl

        logger.info("By market:")
        for market, stats in by_market.items():
            logger.info(
                f"  {market}: {stats['count']} bets, "
                f"{stats['wins']/stats['count']*100:.0f}% wins, "
                f"P/L: {stats['pnl']:+.2f}"
            )


def settle_predictions(days: int = 7):
    """Settle predictions by fetching actual results for completed fixtures."""
    from src.storage.models import PredictionRecord, Fixture
    from scripts.auto_bet import _get_market_result
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    settled = 0
    with get_session() as s:
        unsettled = s.execute(
            select(PredictionRecord, Fixture)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.settled == False)
            .where(Fixture.status.in_(["FT", "AET", "PEN"]))
        ).all()
        
        for pred, fixture in unsettled:
            actual = _get_market_result(fixture, pred.market)
            if actual:
                pred.actual_outcome = actual
                pred.won = (pred.predicted_outcome == actual)
                pred.settled = True
                pred.settled_at = datetime.utcnow()
                settled += 1
        
        s.commit()
    
    logger.info(f"Settled {settled} predictions")
    return settled


def main():
    parser = argparse.ArgumentParser(description="Settle completed bets")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--days", type=int, default=7, help="Days to look back")
    parser.add_argument("--status", action="store_true", help="Show bankroll status")
    parser.add_argument("--results", action="store_true", help="Show recent results")
    parser.add_argument("--results-days", type=int, default=30, help="Days for results")
    parser.add_argument("--predictions", action="store_true", help="Settle predictions only")
    parser.add_argument("--all", action="store_true", help="Settle bets and predictions")
    args = parser.parse_args()

    if args.status:
        show_bankroll()
    elif args.results:
        show_results(days=args.results_days)
    elif args.predictions:
        settle_predictions(days=args.days)
    elif args.all:
        settle_bets(dry_run=args.dry_run, days=args.days)
        settle_predictions(days=args.days)
    else:
        settle_bets(dry_run=args.dry_run, days=args.days)


if __name__ == "__main__":
    main()
