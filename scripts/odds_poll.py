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
STALE_THRESHOLD_HOURS = 2.0   # Re-poll when odds are older than this
NEAR_KICKOFF_HOURS = 6.0      # Unsettled-prediction bucket: only within this window


def find_fixtures_needing_odds(s, league_ids=None):
    """Find fixtures that need fresh odds polling, ordered by priority then kickoff date.

    Priority (within each bucket, earlier kickoff wins):
    1. Fixtures with pending placed bets (skip if polled within STALE_THRESHOLD_HOURS)
    2. PredictionRecords with EV > threshold (skip if polled within STALE_THRESHOLD_HOURS)
    3. Unsettled predictions within NEAR_KICKOFF_HOURS (skip if polled within STALE_THRESHOLD_HOURS)
    4. Upcoming fixtures with no odds at all (bootstrap)

    The staleness filter prevents re-polling ~1900 fixtures every hour — without it
    steady-state odds polling would consume ~140k calls/day vs ~4k with the filter.
    """
    now = datetime.utcnow()
    cutoff = now + timedelta(hours=KICKOFF_HOURS_AHEAD)
    stale_cutoff = now - timedelta(hours=STALE_THRESHOLD_HOURS)
    near_kickoff_cutoff = now + timedelta(hours=NEAR_KICKOFF_HOURS)

    # Fetch all candidate fixtures in date order in one query
    fix_query = select(Fixture.id, Fixture.date).where(
        Fixture.date >= now,
        Fixture.date <= cutoff,
        Fixture.status.in_(['NS', '1H', '2H', 'HT', 'ET']),
    )
    if league_ids:
        fix_query = fix_query.where(Fixture.league_id.in_(league_ids))

    candidate_rows = s.execute(fix_query.order_by(Fixture.date)).all()
    if not candidate_rows:
        return []

    all_ids = [r[0] for r in candidate_rows]
    date_by_id = {r[0]: r[1] for r in candidate_rows}

    # Batch: fixtures with pending bets
    pending_bet_ids = {
        r[0] for r in s.execute(
            select(PlacedBet.fixture_id)
            .where(PlacedBet.fixture_id.in_(all_ids), PlacedBet.settled == False)
            .distinct()
        ).all()
    }

    # Batch: fixtures with high-EV unsettled predictions
    high_ev_ids = {
        r[0] for r in s.execute(
            select(PredictionRecord.fixture_id)
            .where(
                PredictionRecord.fixture_id.in_(all_ids),
                PredictionRecord.settled == False,
                PredictionRecord.ev >= EV_THRESHOLD,
            )
            .distinct()
        ).all()
    }

    # Batch: fixtures with any unsettled predictions
    unsettled_ids = {
        r[0] for r in s.execute(
            select(PredictionRecord.fixture_id)
            .where(
                PredictionRecord.fixture_id.in_(all_ids),
                PredictionRecord.settled == False,
            )
            .distinct()
        ).all()
    }

    # Batch: fixtures that already have odds (exclude from bootstrap bucket)
    has_odds_ids = {
        r[0] for r in s.execute(
            select(FixtureOdds.fixture_id)
            .where(FixtureOdds.fixture_id.in_(all_ids))
            .distinct()
        ).all()
    }

    # Batch: fixtures polled within STALE_THRESHOLD_HOURS (skip these in priority buckets)
    recently_polled_ids = {
        r[0] for r in s.execute(
            select(FixtureOdds.fixture_id)
            .where(
                FixtureOdds.fixture_id.in_(all_ids),
                FixtureOdds.fetched_at > stale_cutoff,
            )
            .distinct()
        ).all()
    }

    # Assign each fixture to its highest-priority bucket
    buckets = {1: [], 2: [], 3: [], 4: []}
    for fix_id in all_ids:  # already date-ordered
        kickoff = date_by_id[fix_id]
        recently = fix_id in recently_polled_ids

        if fix_id in pending_bet_ids:
            if not recently:
                buckets[1].append(fix_id)
        elif fix_id in high_ev_ids:
            if not recently:
                buckets[2].append(fix_id)
        elif fix_id in unsettled_ids:
            if kickoff <= near_kickoff_cutoff and not recently:
                buckets[3].append(fix_id)
        elif fix_id not in has_odds_ids:
            buckets[4].append(fix_id)

    result = buckets[1] + buckets[2] + buckets[3] + buckets[4]
    logger.info(
        f"[ODDS] fixtures needing poll: pending_bets={len(buckets[1])} "
        f"high_ev={len(buckets[2])} unsettled_near_ko={len(buckets[3])} bootstrap={len(buckets[4])}"
    )
    return result


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


_CLV_FIELD_MAP = {
    "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
}


def capture_closing_lines(s, fixture_ids=None, dry_run=False):
    """Snapshot closing odds for open placed bets near kickoff — Closing Line Value.

    CLV compares the price you bet at to the price the market settles on just
    before kickoff: a same-day signal of whether a claimed "edge" was real
    foresight (the market moved toward your side after you bet — positive CLV)
    or model error (the market moved away — negative CLV), instead of waiting
    weeks for the match to settle and the bet to grade.

    Convention (odds-ratio, the standard "beat the close" measure):
        clv_pct = (your_odds - closing_odds) / closing_odds
    Positive  → you got longer odds than the closing line (beat the close — good).
    Negative  → the market shortened away from your price (bad).

    Only fires for fixtures that are within NEAR_KICKOFF_HOURS of kickoff and
    haven't started yet ('NS') — any later and "closing" odds aren't truly
    closing; any earlier and the line can still move a lot before kickoff.
    """
    now = datetime.utcnow()
    near_cutoff = now + timedelta(hours=NEAR_KICKOFF_HOURS)

    query = (
        select(PlacedBet, Fixture)
        .join(Fixture, Fixture.id == PlacedBet.fixture_id)
        .where(
            PlacedBet.settled == False,
            PlacedBet.closing_odds.is_(None),
            Fixture.status == 'NS',
            Fixture.date >= now,
            Fixture.date <= near_cutoff,
        )
    )
    if fixture_ids:
        query = query.where(PlacedBet.fixture_id.in_(fixture_ids))

    rows = s.execute(query).all()
    if not rows:
        return 0

    captured = 0
    for bet, fixture in rows:
        bet_type = MARKET_BET_TYPE_MAP.get(bet.market, bet.market)
        odds_row = s.execute(
            select(FixtureOdds).where(
                FixtureOdds.fixture_id == bet.fixture_id,
                FixtureOdds.bet_type == bet_type,
            )
        ).scalars().first()
        if not odds_row:
            continue

        odd_field = _CLV_FIELD_MAP.get(bet.market, {}).get(bet.outcome)
        if not odd_field:
            continue

        closing_odds = getattr(odds_row, odd_field, None)
        if not closing_odds or closing_odds < 1.01:
            continue

        clv_pct = (bet.odds - closing_odds) / closing_odds

        if dry_run:
            logger.info(
                f"  [DRY RUN] Would capture CLV for bet {bet.id} "
                f"({bet.market}/{bet.outcome}): your_odds={bet.odds:.2f} "
                f"closing_odds={closing_odds:.2f} clv_pct={clv_pct:+.3f}"
            )
            continue

        bet.closing_odds = closing_odds
        bet.closing_implied_prob = 1.0 / closing_odds
        bet.clv_pct = clv_pct
        captured += 1

    if captured and not dry_run:
        s.commit()
        logger.info(f"[CLV] Captured closing lines for {captured} bet(s)")

    return captured


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

    # Cap by CLI flag first, then by remaining quota — no other hard limit
    if args.max_fixtures:
        fixture_ids = fixture_ids[:args.max_fixtures]
    fixture_ids = fixture_ids[:remaining // 3]
    logger.info(f"Polling odds for {len(fixture_ids)} fixtures (quota remaining: {remaining})")

    with get_session() as s:
        updated_odds = poll_and_update_odds(s, client, fixture_ids, args.dry_run)
        logger.info(f"Updated {updated_odds} odds rows")

        recalculated = recalculate_prediction_ev(s, fixture_ids, args.dry_run)
        logger.info(f"Recalculated EV for {recalculated} predictions")

        clv_captured = capture_closing_lines(s, fixture_ids, args.dry_run)
        if clv_captured:
            logger.info(f"Captured CLV for {clv_captured} placed bet(s)")

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