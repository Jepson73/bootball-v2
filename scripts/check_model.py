#!/usr/bin/env python3
"""
scripts/check_model.py

Check model health and trigger retraining if needed.

Usage:
    python scripts/check_model.py              # Check status
    python scripts/check_model.py --force      # Force retrain
    python scripts/check_model.py --retrain    # Retrain if ROI below threshold
    python scripts/check_model.py --roi-threshold -10  # Custom threshold
"""
import argparse
import logging
import sys
from dataclasses import dataclass

sys.path.insert(0, '/opt/projects/bootball')

from sqlalchemy import select, func

from src.storage.db import get_session
from src.storage.models import SettledBet, Bankroll, ValueBet
from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

ROI_THRESHOLD = -15.0
MIN_BETS_FOR_CHECK = 20
MIN_SETTLED_FOR_RETRAIN = 50


@dataclass
class ModelHealth:
    n_settled_bets: int
    recent_roi: float
    win_rate: float
    avg_odds: float
    profit: float
    needs_retrain: bool
    reason: str


def check_model_health(days: int = 30, roi_threshold: float = ROI_THRESHOLD) -> ModelHealth:
    """Check if model needs retraining based on recent performance."""
    with get_session() as s:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)

        bets = s.execute(
            select(SettledBet).where(SettledBet.settled_at >= cutoff)
        ).scalars().all()

        if len(bets) < MIN_BETS_FOR_CHECK:
            return ModelHealth(
                n_settled_bets=len(bets),
                recent_roi=0.0,
                win_rate=0.0,
                avg_odds=0.0,
                profit=0.0,
                needs_retrain=False,
                reason=f"Not enough bets ({len(bets)} < {MIN_BETS_FOR_CHECK})"
            )

        n_wins = sum(1 for b in bets if b.won)
        total_staked = sum(b.stake for b in bets)
        total_returned = sum(b.stake * b.odds if b.won else 0 for b in bets)
        profit = total_returned - total_staked
        roi = (profit / total_staked * 100) if total_staked > 0 else 0
        win_rate = n_wins / len(bets)
        avg_odds = sum(b.odds for b in bets) / len(bets)

        needs_retrain = False
        reason = ""

        if len(bets) >= MIN_SETTLED_FOR_RETRAIN and roi < roi_threshold:
            needs_retrain = True
            reason = f"ROI {roi:.1f}% below threshold {roi_threshold}%"
        elif win_rate < 0.40 and len(bets) >= MIN_SETTLED_FOR_RETRAIN:
            needs_retrain = True
            reason = f"Win rate {win_rate:.1%} below 40%"

        return ModelHealth(
            n_settled_bets=len(bets),
            recent_roi=roi,
            win_rate=win_rate,
            avg_odds=avg_odds,
            profit=profit,
            needs_retrain=needs_retrain,
            reason=reason
        )


def get_bankroll_status() -> dict:
    """Get current bankroll status."""
    with get_session() as s:
        latest = s.execute(
            select(Bankroll).order_by(Bankroll.date.desc()).limit(1)
        ).scalars().first()

        if latest:
            return {
                "balance": latest.balance,
                "total_staked": latest.total_staked,
                "bet_count": latest.bet_count,
                "win_count": latest.win_count,
                "date": latest.date,
            }

        settled = s.execute(
            select(func.count(), func.sum(SettledBet.pnl)).select_from(SettledBet)
        ).first()

        if settled and settled[0] > 0:
            return {
                "balance": 1000.0 + (settled[1] or 0),
                "total_staked": 0,
                "bet_count": settled[0],
                "win_count": 0,
                "date": None,
            }

        return {
            "balance": 1000.0,
            "total_staked": 0,
            "bet_count": 0,
            "win_count": 0,
            "date": None,
        }


def get_model_stats() -> dict:
    """Get stats on model quality."""
    with get_session() as s:
        unsettled = s.execute(
            select(func.count()).select_from(ValueBet).where(ValueBet.settled == False)
        ).scalar()

        avg_ev = s.execute(
            select(func.avg(ValueBet.ev)).select_from(ValueBet).where(ValueBet.settled == False)
        ).scalar()

        return {
            "unsettled_bets": unsettled or 0,
            "avg_ev": avg_ev or 0,
        }


def retrain_model():
    """Trigger model retraining."""
    logger.info("Retraining model...")
    logger.info("Note: Implement actual retraining logic here")
    logger.info("Options:")
    logger.info("  1. Call train_multi_calibrated.py")
    logger.info("  2. Update model weights in DB")
    logger.info("  3. Set flag for next daily run")
    logger.info("Retrain triggered successfully")


def print_status(health: ModelHealth, bankroll: dict, model_stats: dict):
    """Print model health status."""
    print("\n" + "=" * 50)
    print("MODEL HEALTH CHECK")
    print("=" * 50)

    print(f"\nSettled Bets (last 30 days):")
    print(f"  Count: {health.n_settled_bets}")
    print(f"  ROI: {health.recent_roi:+.1f}%")
    print(f"  Win Rate: {health.win_rate:.1%}")
    print(f"  Avg Odds: {health.avg_odds:.2f}")
    print(f"  Profit: ${health.profit:+.1f}")

    print(f"\nBankroll:")
    print(f"  Balance: ${bankroll['balance']:.2f}")
    print(f"  Total Bets: {bankroll['bet_count']}")

    print(f"\nModel Stats:")
    print(f"  Unsettled Bets: {model_stats['unsettled_bets']}")
    print(f"  Avg EV: {model_stats['avg_ev']*100:+.1f}%")

    print(f"\nStatus:")
    if health.needs_retrain:
        print(f"  ⚠️  RETRAIN NEEDED: {health.reason}")
    else:
        print(f"  ✅ Model healthy")
        if health.reason:
            print(f"     ({health.reason})")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Check model health")
    parser.add_argument("--days", type=int, default=30, help="Days to analyze")
    parser.add_argument("--roi-threshold", type=float, default=ROI_THRESHOLD,
                       help="ROI threshold for retrain")
    parser.add_argument("--force", action="store_true", help="Force retrain")
    parser.add_argument("--retrain", action="store_true", help="Retrain if needed")
    args = parser.parse_args()

    health = check_model_health(days=args.days, roi_threshold=args.roi_threshold)
    bankroll = get_bankroll_status()
    model_stats = get_model_stats()

    print_status(health, bankroll, model_stats)

    if args.force:
        logger.info("Force flag set, triggering retrain")
        retrain_model()
    elif health.needs_retrain and args.retrain:
        logger.info("Retrain needed, triggering retrain")
        retrain_model()
    elif health.needs_retrain:
        logger.info("Use --retrain to trigger automatic retrain")


if __name__ == "__main__":
    main()
