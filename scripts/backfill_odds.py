#!/usr/bin/env python3
"""
scripts/backfill_odds.py

Fetch odds for all fixtures that have predictions but no odds.
Run this once to backfill historical data.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Fixture, FixtureOdds, PredictionRecord
from sqlalchemy import select
from src.betting.ev import expected_value
from src.models.calibrator import calibrate_prediction

MARKET_ODDS_MAP = {
    "h2h": ("h2h", {"1": "odd_home", "X": "odd_draw", "2": "odd_away"}),
    "btts": ("btts", {"Yes": "odd_btts_yes", "No": "odd_btts_no"}),
    "ou25": ("over_under", {"Over": "odd_over", "Under": "odd_under"}),
    "ou15": ("over_under", {"Over": "odd_over15", "Under": "odd_under15"}),
}

BET_TYPE_IDS = {
    "h2h": 1,
    "btts": 8,
    "over_under": 5,
}


def fetch_odds_for_fixture(client, s, fixture_id):
    """Fetch odds from API and store in FixtureOdds."""
    import datetime
    
    for bet_name, bet_id in BET_TYPE_IDS.items():
        try:
            odds_data = client.get_odds(fixture_id=fixture_id, bet_type=bet_id)
        except Exception as e:
            continue

        if not odds_data:
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

                    existing = s.execute(
                        select(FixtureOdds).where(
                            FixtureOdds.fixture_id == fixture_id,
                            FixtureOdds.bookmaker == bookmaker,
                            FixtureOdds.bet_type == bet_name,
                        )
                    ).scalars().first()

                    if existing:
                        for field, value in update_data.items():
                            if value is not None:
                                setattr(existing, field, value)
                        existing.fetched_at = datetime.datetime.utcnow()
                    else:
                        s.add(FixtureOdds(
                            fixture_id=fixture_id,
                            bookmaker=bookmaker,
                            bet_type=bet_name,
                            **{k: v for k, v in update_data.items() if v is not None},
                        ))

    s.commit()


def update_prediction_records(s, fixture_ids):
    """Update PredictionRecords with odds/EV for given fixtures."""
    updated = 0
    for fix_id in fixture_ids:
        preds = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.fixture_id == fix_id,
                PredictionRecord.settled == False,
                PredictionRecord.odds_decimal == None,
            )
        ).scalars().all()

        for p in preds:
            market_info = MARKET_ODDS_MAP.get(p.market)
            if not market_info:
                continue

            bet_type, field_map = market_info

            odds_row = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fix_id,
                    FixtureOdds.bet_type == bet_type,
                )
            ).scalars().first()

            if not odds_row:
                continue

            odd_field = field_map.get(p.predicted_outcome)
            if not odd_field:
                continue

            odds_decimal = getattr(odds_row, odd_field, None)
            if not odds_decimal or odds_decimal <= 0:
                continue

            ev = expected_value(p.our_prob, odds_decimal)
            implied_prob = 1.0 / odds_decimal
            edge = (p.our_prob - implied_prob) * 100

            calibration = calibrate_prediction(p.market, p.our_prob)
            calibrated_prob = calibration.calibrated_prob

            p.odds_decimal = odds_decimal
            p.ev = ev
            p.calibrated_prob = calibrated_prob
            p.implied_prob = implied_prob
            p.edge = edge
            p.bookmaker = odds_row.bookmaker
            updated += 1

    s.commit()
    return updated


def main():
    client = APIFootballClient()

    with get_session() as s:
        fixture_ids = s.execute(
            select(PredictionRecord.fixture_id)
            .where(PredictionRecord.settled == False)
            .where(PredictionRecord.odds_decimal == None)
            .where(PredictionRecord.our_prob != None)
            .distinct()
        ).scalars().all()

    print(f"Fixtures needing odds: {len(fixture_ids)}")

    for i, fix_id in enumerate(fixture_ids):
        if i % 50 == 0:
            print(f"Progress: {i}/{len(fixture_ids)}")

        with get_session() as s:
            fetch_odds_for_fixture(client, s, fix_id)
            updated = update_prediction_records(s, [fix_id])

    print(f"Done! Processed {len(fixture_ids)} fixtures")


if __name__ == "__main__":
    main()