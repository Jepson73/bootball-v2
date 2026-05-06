#!/usr/bin/env python3
"""
scripts/odds_poll.py

Selective odds polling to keep predictions fresh:
1. Find fixtures needing fresh odds (pending bets, high EV, upcoming kickoff)
2. Fetch fresh odds from API-Football
3. Update FixtureOdds table
4. Recalculate EV for PredictionRecords

Usage:
    python scripts/odds_poll.py              # Normal run
    python scripts/odds_poll.py --dry-run    # Preview without changes
    python scripts/odds_poll.py --leagues 39 # Specific leagues only
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from config.leagues import ALL_LEAGUE_IDS, LEAGUES
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, PlacedBet
from src.betting.ev import expected_value
from src.betting.alerts import BettingAlerts, BetAlert
from src.models.calibrator import calibrate_prediction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

EV_THRESHOLD = 0.05
KICKOFF_HOURS_AHEAD = 168  # 7 days — match the prediction window


def find_fixtures_needing_odds(s, league_ids=None):
    """Find fixtures that need fresh odds polling.

    Priority:
    1. Fixtures with pending placed bets
    2. PredictionRecords with EV > threshold
    3. Fixtures with unsettled predictions
    4. Upcoming NS fixtures with no odds at all (bootstrap initial odds)
    """
    now = datetime.utcnow()
    cutoff = now + timedelta(hours=KICKOFF_HOURS_AHEAD)

    query = select(Fixture).where(
        Fixture.date >= now,
        Fixture.date <= cutoff,
        Fixture.status.in_(['NS', '1H', '2H', 'HT', 'ET']),
    )

    if league_ids:
        query = query.where(Fixture.league_id.in_(league_ids))

    fixtures = s.execute(query.order_by(Fixture.date)).scalars().all()

    fixtures_needing = set()
    fixtures_without_odds = []

    for fix in fixtures:
        pending_bets = s.execute(
            select(func.count()).select_from(PlacedBet)
            .where(PlacedBet.fixture_id == fix.id)
            .where(PlacedBet.settled == False)
        ).scalar() or 0

        if pending_bets > 0:
            fixtures_needing.add(fix.id)
            logger.debug(f"Fixture {fix.id}: {pending_bets} pending bets")
            continue

        high_ev_preds = s.execute(
            select(func.count()).select_from(PredictionRecord)
            .where(PredictionRecord.fixture_id == fix.id)
            .where(PredictionRecord.settled == False)
            .where(PredictionRecord.ev >= EV_THRESHOLD)
        ).scalar() or 0

        if high_ev_preds > 0:
            fixtures_needing.add(fix.id)
            logger.debug(f"Fixture {fix.id}: {high_ev_preds} high-EV predictions")
            continue

        unsettled_preds = s.execute(
            select(func.count()).select_from(PredictionRecord)
            .where(PredictionRecord.fixture_id == fix.id)
            .where(PredictionRecord.settled == False)
        ).scalar() or 0

        if unsettled_preds > 0:
            fixtures_needing.add(fix.id)
            logger.debug(f"Fixture {fix.id}: {unsettled_preds} unsettled predictions")
            continue

        # Bootstrap: add fixtures with no odds yet (lowest priority)
        has_odds = s.execute(
            select(func.count()).select_from(FixtureOdds)
            .where(FixtureOdds.fixture_id == fix.id)
        ).scalar() or 0

        if has_odds == 0:
            fixtures_without_odds.append(fix.id)

    # Fill remaining capacity with no-odds fixtures (ordered by kickoff proximity)
    fixtures_needing.update(fixtures_without_odds)

    return list(fixtures_needing)


def poll_and_update_odds(s, client, fixture_ids, dry_run=False):
    """Fetch fresh odds for given fixtures and update DB."""
    if not fixture_ids:
        logger.info("No fixtures need odds polling")
        return 0

    updated = 0

    for fix_id in fixture_ids:
        logger.info(f"Polling odds for fixture {fix_id}")

        bet_types = {
            "h2h": 1,
            "btts": 8,
            "over_under": 5,
        }

        for bet_name, bet_id in bet_types.items():
            try:
                odds_data = client.get_odds(fixture_id=fix_id, bet_type=bet_id)
            except Exception as e:
                logger.warning(f"Failed to get {bet_name} odds for fixture {fix_id}: {e}")
                continue

            if not odds_data:
                logger.debug(f"No {bet_name} odds returned for fixture {fix_id}")
                continue

            for response in odds_data:
                bookmakers = response.get('bookmakers', [])
                for bm in bookmakers:
                    bookmaker = bm.get('name', 'Unknown')
                    bets = bm.get('bets', [])
                    
                    for bet in bets:
                        bet_values = bet.get('values', [])
                        if not bet_values:
                            continue

                        odds_dict = {}
                        for v in bet_values:
                            label = v.get('value', '')
                            odd_value = v.get('odd')
                            if label and odd_value:
                                odds_dict[label] = float(odd_value)

                        if not odds_dict:
                            continue

                        if dry_run:
                            logger.info(f"  [DRY RUN] Would update {bet_name} odds from {bookmaker}")
                            continue

                        existing = s.execute(
                            select(FixtureOdds).where(
                                FixtureOdds.fixture_id == fix_id,
                                FixtureOdds.bookmaker == bookmaker,
                                FixtureOdds.bet_type == bet_name,
                            )
                        ).scalars().first()

                        update_data = {
                            'odd_home': odds_dict.get('Home'),
                            'odd_draw': odds_dict.get('Draw'),
                            'odd_away': odds_dict.get('Away'),
                            'odd_over': odds_dict.get('Over 2.5'),
                            'odd_under': odds_dict.get('Under 2.5'),
                            'odd_btts_yes': odds_dict.get('Yes'),
                            'odd_btts_no': odds_dict.get('No'),
                            'odd_over15': odds_dict.get('Over 1.5'),
                            'odd_under15': odds_dict.get('Under 1.5'),
                        }

                        if existing:
                            for field, value in update_data.items():
                                setattr(existing, field, value)
                            existing.fetched_at = datetime.utcnow()
                        else:
                            s.add(FixtureOdds(
                                fixture_id=fix_id,
                                bookmaker=bookmaker,
                                bet_type=bet_name,
                                **{k: v for k, v in update_data.items() if v is not None},
                            ))

                updated += 1

        s.commit()

    return updated


def recalculate_prediction_ev(s, fixture_ids, dry_run=False):
    """Recalculate EV for PredictionRecords when odds change."""
    field_map = {
        "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
        "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
        "ou25": {"Over": "odd_over", "Under": "odd_under"},
        "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
    }

    updated = 0

    for fix_id in fixture_ids:
        preds = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.fixture_id == fix_id,
                PredictionRecord.settled == False,
            )
        ).scalars().all()

        for pred in preds:
            bet_type = MARKET_BET_TYPE_MAP.get(pred.market, pred.market)
            odds_row = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fix_id,
                    FixtureOdds.bet_type == bet_type,
                )
            ).scalars().first()

            if not odds_row:
                continue

            market_fields = field_map.get(pred.market, {})
            odd_field = market_fields.get(pred.predicted_outcome)
            if not odd_field:
                continue

            odds_decimal = getattr(odds_row, odd_field, None)
            if not odds_decimal or odds_decimal <= 0:
                continue

            calibration = calibrate_prediction(pred.market, pred.our_prob)
            calibrated_prob = calibration.calibrated_prob
            ev = expected_value(calibrated_prob, odds_decimal)
            implied_prob = 1.0 / odds_decimal
            edge = (calibrated_prob - implied_prob) * 100

            if dry_run:
                logger.info(f"  [DRY RUN] Would update pred {pred.id}: EV={ev:.3f}, calibrated_prob={calibrated_prob:.3f}")
            else:
                pred.odds_decimal = odds_decimal
                pred.ev = ev
                pred.calibrated_prob = calibrated_prob
                pred.implied_prob = implied_prob
                pred.edge = edge
                pred.bookmaker = odds_row.bookmaker

            updated += 1

        if not dry_run:
            s.commit()

    return updated


MARKET_BET_TYPE_MAP = {
        "h2h": "h2h",
        "btts": "btts",
        "ou25": "over_under",
        "ou15": "over_under",
    }

MARKET_MIN_ODDS = {
    "h2h": 1.5,
    "btts": 1.5,
    "ou25": 1.5,
    "ou15": 0,  # Skip ou15 entirely
}

def find_new_value_bets(s, fixture_ids, min_ev=0.05, min_odds=1.5, limit=5):
    """Find new value bet opportunities after odds update."""
    field_map = {
        "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
        "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
        "ou25": {"Over": "odd_over", "Under": "odd_under"},
        "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
    }

    from config.leagues import LEAGUES
    from src.storage.models import Team

    new_bets = []

    for fix_id in fixture_ids:
        fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
        if not fix:
            continue

        home_team = s.execute(select(Team).where(Team.id == fix.home_team_id)).scalar_one_or_none()
        away_team = s.execute(select(Team).where(Team.id == fix.away_team_id)).scalar_one_or_none()
        if not home_team or not away_team:
            continue

        home_name = home_team.name
        away_name = away_team.name

        preds = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.fixture_id == fix_id,
                PredictionRecord.settled == False,
            )
        ).scalars().all()

        for pred in preds:
            # Skip ou15 entirely
            if pred.market == "ou15":
                continue
                
            # Use market-specific min_odds
            market_min = MARKET_MIN_ODDS.get(pred.market, min_odds)
            if market_min == 0:
                continue
            
            # Map market to correct bet_type
            bet_type = MARKET_BET_TYPE_MAP.get(pred.market, pred.market)
            
            odds_row = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fix_id,
                    FixtureOdds.bet_type == bet_type,
                )
            ).scalars().first()

            if not odds_row:
                continue

            market_fields = field_map.get(pred.market, {})
            odd_field = market_fields.get(pred.predicted_outcome)
            if not odd_field:
                continue

            odds_decimal = getattr(odds_row, odd_field, None)
            if not odds_decimal or odds_decimal < market_min:
                continue

            ev = expected_value(pred.our_prob, odds_decimal)
            if ev < min_ev:
                continue

            league_name = LEAGUES.get(fix.league_id, {}).get('name', '')
            kickoff_utc = fix.date
            kickoff_local = kickoff_utc.replace(tzinfo=None).strftime('%H:%M') if kickoff_utc else ''

            new_bets.append(BetAlert(
                market=pred.market,
                home_team=home_name,
                away_team=away_name,
                outcome=pred.predicted_outcome,
                odds=odds_decimal,
                ev=ev,
                kelly=0.05,
                league=league_name,
                fixture_date=kickoff_local,
            ))

    new_bets.sort(key=lambda x: x.ev, reverse=True)
    return new_bets[:limit]


def main():
    parser = argparse.ArgumentParser(description="Selective odds polling")
    parser.add_argument('--dry-run', action='store_true', help='Preview without changes')
    parser.add_argument('--leagues', type=str, help='Comma-separated league IDs')
    parser.add_argument('--max-fixtures', type=int, default=50, help='Max fixtures to poll per run')
    args = parser.parse_args()

    league_ids = None
    if args.leagues:
        league_ids = [int(x) for x in args.leagues.split(",")]

    init_db()

    client = APIFootballClient()
    remaining = calls_remaining_today()
    logger.info(f"API calls remaining today: {remaining}")

    if remaining < 50:
        logger.warning(f"Low API calls ({remaining}), skipping odds poll")
        return

    with get_session() as s:
        fixture_ids = find_fixtures_needing_odds(s, league_ids)

    if not fixture_ids:
        logger.info("No fixtures need odds polling")
        return

    fixture_ids = fixture_ids[:args.max_fixtures]
    logger.info(f"Polling odds for {len(fixture_ids)} fixtures")

    estimated_calls = len(fixture_ids) * 3
    if remaining < estimated_calls:
        logger.warning(f"Only {remaining} calls remaining, need ~{estimated_calls}")
        fixture_ids = fixture_ids[:remaining // 3]
        logger.info(f"Reduced to {len(fixture_ids)} fixtures")

    with get_session() as s:
        updated_odds = poll_and_update_odds(s, client, fixture_ids, args.dry_run)
        logger.info(f"Updated {updated_odds} odds rows")

        recalculated = recalculate_prediction_ev(s, fixture_ids, args.dry_run)
        logger.info(f"Recalculated EV for {recalculated} predictions")

        if not args.dry_run and updated_odds > 0:
            new_value_bets = find_new_value_bets(s, fixture_ids, min_ev=0.05, min_odds=1.2)
            if new_value_bets:
                alerts = BettingAlerts(
                    channels=["discord"],
                    min_ev=0,
                    min_odds=0,
                    min_kelly=0,
                )
                for bet in new_value_bets:
                    alerts.send_bet_alert(bet)
                logger.info(f"Sent {len(new_value_bets)} new value bet alerts")


if __name__ == "__main__":
    main()