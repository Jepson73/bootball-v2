#!/usr/bin/env python3
"""
scripts/send_alerts.py

Send top value bets to Discord webhook.

Usage:
    python scripts/send_alerts.py              # Send top 5 bets
    python scripts/send_alerts.py --top 10     # Send top 10 bets
    python scripts/send_alerts.py --dry-run   # Preview without sending
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from config.settings import settings
from config.leagues import ALL_LEAGUE_IDS
from src.alerts import discord_alerts, create_bet_alert
from src.storage.db import get_session
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, League, Team

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def get_top_bets(session, top_n: int = 5, min_ev: float = 0.05):
    """Get top N value bets from predictions.

    Args:
        session: Database session
        top_n: Number of top bets to return
        min_ev: Minimum EV threshold

    Returns:
        List of BetAlert objects
    """
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)

    # Get distinct fixtures (don't join, which duplicates rows)
    fixture_ids = session.execute(
        select(Fixture.id)
        .where(Fixture.status == "NS")
        .where(Fixture.date >= now)
        .where(Fixture.date < tomorrow)
        .where(Fixture.league_id.in_(ALL_LEAGUE_IDS))
    ).scalars().all()

    fixture_ids = list(set(fixture_ids))  # Deduplicate

    bets = []

    for fix_id in fixture_ids:
        fixture = session.execute(
            select(Fixture).where(Fixture.id == fix_id)
        ).scalar_one_or_none()

        if not fixture:
            continue

        league = session.execute(
            select(League).where(League.id == fixture.league_id)
        ).scalar_one_or_none()

        home_team = session.execute(
            select(Team).where(Team.id == fixture.home_team_id)
        ).scalar_one_or_none()

        away_team = session.execute(
            select(Team).where(Team.id == fixture.away_team_id)
        ).scalar_one_or_none()

        if not all([league, home_team, away_team]):
            continue

        preds = session.execute(
            select(PredictionRecord).where(PredictionRecord.fixture_id == fix_id)
        ).scalars().all()
        pred_dict = {p.market: p for p in preds}

        all_odds = session.execute(
            select(FixtureOdds).where(FixtureOdds.fixture_id == fix_id)
        ).scalars().all()
        odds_by_type = {row.bet_type: row for row in all_odds}

        btts_row = odds_by_type.get('btts')
        ou_row = odds_by_type.get('over_under')
        h2h_row = odds_by_type.get('h2h')

        markets_to_check = [
            ('btts', btts_row, 'Yes', 'odd_btts_yes'),
            ('ou25', ou_row, 'Over', 'odd_over'),
            ('ou15', ou_row, 'Over', 'odd_over15'),
        ]

        for market, row, outcome, odds_field in markets_to_check:
            if row is None:
                continue

            pred = pred_dict.get(market)
            if pred is None:
                continue

            odds = getattr(row, odds_field, None)
            if odds is None or odds <= 0:
                continue

            our_prob = pred.our_prob
            if our_prob is None:
                continue

            ev = (our_prob * odds) - 1

            if ev < min_ev:
                continue

            bet = create_bet_alert(
                fixture_id=fix_id,
                home_team=home_team.name,
                away_team=away_team.name,
                home_logo=home_team.logo_url,
                away_logo=away_team.logo_url,
                league=league.name,
                league_flag=league.flag,
                market=market,
                outcome=outcome,
                odds=odds,
                our_prob=our_prob,
                ev=ev,
                kickoff=fixture.date,
            )
            bets.append(bet)

    bets.sort(key=lambda x: x.ev, reverse=True)
    return bets[:top_n]


def main():
    parser = argparse.ArgumentParser(description="Send top bets to Discord")
    parser.add_argument("--top", type=int, default=5, help="Number of top bets to send")
    parser.add_argument("--min-ev", type=float, default=0.05, help="Minimum EV threshold")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    args = parser.parse_args()

    logger.info(f"Fetching top {args.top} bets with EV >= {args.min_ev:.1%}")

    with get_session() as session:
        bets = get_top_bets(session, top_n=args.top, min_ev=args.min_ev)

    if not bets:
        logger.warning("No bets found matching criteria")
        return

    logger.info(f"Found {len(bets)} qualifying bets")

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN - Would send these bets to Discord:")
        print("=" * 60 + "\n")
        for i, bet in enumerate(bets, 1):
            print(f"{i}. {bet.home_team} vs {bet.away_team}")
            print(f"   {bet.league} | {bet.kickoff}")
            print(f"   {bet.market_display}: {bet.outcome} @ {bet.odds:.2f}")
            print(f"   Prob: {bet.our_prob:.1%} | EV: {bet.ev:.1%} {bet.ev_stars}")
            print()
        return

    success = discord_alerts.send_bet_alerts(bets)

    if success:
        logger.info("Alerts sent to Discord!")
    else:
        logger.error("Failed to send Discord alerts")


if __name__ == "__main__":
    main()
