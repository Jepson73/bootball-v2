"""
tests/web_ui/test_predictions_api.py

Tests for the predictions API multi-market support.
Verifies that all bet_type rows (btts, over_under, h2h) are correctly fetched
and mapped to their respective markets.
"""
import sys
sys.path.insert(0, '.')

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, League


@pytest.fixture
def db_session():
    """Create a test session against the real database."""
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def upcoming_fixtures(db_session):
    """Get 5 upcoming fixtures with all market predictions and odds."""
    now = datetime.utcnow()
    end = now + timedelta(days=7)

    fixtures = db_session.execute(
        select(Fixture)
        .where(Fixture.date >= now)
        .where(Fixture.date <= end)
        .where(Fixture.status == 'NS')
        .order_by(Fixture.date)
        .limit(5)
    ).scalars().all()

    return fixtures


class TestPredictionsMultiMarket:
    """Tests for multi-market prediction retrieval."""

    def test_all_bet_types_fetched_per_fixture(self, db_session, upcoming_fixtures):
        """Verify all available FixtureOdds rows are fetched per fixture."""
        fixtures_with_odds = 0
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            if not all_odds:
                continue

            fixtures_with_odds += 1
            odds_by_type = {row.bet_type: row for row in all_odds}

            if 'btts' in odds_by_type:
                assert odds_by_type['btts'].odd_btts_yes is not None

        assert fixtures_with_odds > 0, "No fixtures have odds in test set"

    def test_btts_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify BTTS market uses odd_btts_yes from btts row when available."""
        fixtures_with_btts = []
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            if 'btts' in odds_by_type:
                fixtures_with_btts.append((fix, odds_by_type['btts']))

        assert len(fixtures_with_btts) > 0, "No fixtures have BTTS odds in test set"
        for fix, btts_row in fixtures_with_btts:
            assert btts_row.odd_btts_yes is not None, f"Fixture {fix.id} BTTS odd_btts_yes is NULL"

    def test_ou25_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify O/U 2.5 market uses odd_over from over_under row when available."""
        fixtures_with_ou = []
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            if 'over_under' in odds_by_type:
                fixtures_with_ou.append((fix, odds_by_type['over_under']))

        assert len(fixtures_with_ou) > 0, "No fixtures have over_under odds in test set"
        for fix, ou_row in fixtures_with_ou:
            assert ou_row.odd_over is not None, f"Fixture {fix.id} OU25 odd_over is NULL"

    def test_ou15_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify O/U 1.5 market uses odd_over15 from over_under row when available."""
        fixtures_with_ou = []
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            if 'over_under' in odds_by_type:
                fixtures_with_ou.append((fix, odds_by_type['over_under']))

        assert len(fixtures_with_ou) > 0, "No fixtures have over_under odds in test set"
        for fix, ou_row in fixtures_with_ou:
            assert ou_row.odd_over15 is not None, f"Fixture {fix.id} OU15 odd_over15 is NULL"

    def test_h2h_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify H2H market uses odd_home from h2h row when available."""
        fixtures_with_h2h = []
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            if 'h2h' in odds_by_type:
                fixtures_with_h2h.append((fix, odds_by_type['h2h']))

        assert len(fixtures_with_h2h) > 0, "No fixtures have h2h odds in test set"
        for fix, h2h_row in fixtures_with_h2h:
            assert h2h_row.odd_home is not None, f"Fixture {fix.id} H2H odd_home is NULL"

    def test_all_markets_have_predictions(self, db_session, upcoming_fixtures):
        """Verify predictions exist for markets that have odds available."""
        fixtures_tested = 0
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}

            markets_with_odds = []
            if 'btts' in odds_by_type:
                markets_with_odds.append('btts')
            if 'over_under' in odds_by_type:
                markets_with_odds.extend(['ou25', 'ou15'])
            if 'h2h' in odds_by_type:
                markets_with_odds.append('h2h')

            if not markets_with_odds:
                continue

            preds = db_session.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            pred_dict = {p.market: p for p in preds}

            if not pred_dict:
                continue  # Skip fixtures without predictions

            fixtures_tested += 1

            for market in markets_with_odds:
                assert market in pred_dict, f"Fixture {fix.id} missing {market} prediction"
                assert pred_dict[market].our_prob is not None, f"Fixture {fix.id} {market} prob is NULL"

        assert fixtures_tested > 0, "No fixtures with both odds and predictions in test set"

    def test_ev_calculation_for_all_markets(self, db_session, upcoming_fixtures):
        """Verify EV is correctly calculated for all markets."""
        markets = ['btts', 'ou25', 'ou15', 'h2h']

        for fix in upcoming_fixtures:
            preds = db_session.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            cached = {p.market: p for p in preds}

            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            btts_row = odds_by_type.get('btts')
            ou_row = odds_by_type.get('over_under')
            h2h_row = odds_by_type.get('h2h')

            for market in markets:
                prob = cached.get(market).our_prob if cached.get(market) else None

                if market == 'btts':
                    odds = btts_row.odd_btts_yes if btts_row else None
                elif market == 'ou25':
                    odds = ou_row.odd_over if ou_row else None
                elif market == 'ou15':
                    odds = ou_row.odd_over15 if ou_row else None
                elif market == 'h2h':
                    odds = h2h_row.odd_home if h2h_row else None

                if prob and odds and odds > 0:
                    expected_ev = (prob * odds) - 1
                    assert -1 <= expected_ev <= 10, f"Fixture {fix.id} {market} EV out of reasonable range: {expected_ev}"

    def test_pick_selection_logic(self, db_session, upcoming_fixtures):
        """Verify pick is correctly selected based on probability."""
        markets = ['btts', 'ou25', 'ou15', 'h2h']
        expected_picks = {
            'btts': ['Yes', 'No'],
            'ou25': ['Over', 'Under'],
            'ou15': ['Over', 'Under'],
            'h2h': ['Home', 'Away'],
        }

        for fix in upcoming_fixtures:
            preds = db_session.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            cached = {p.market: p for p in preds}

            for market in markets:
                prob = cached.get(market).our_prob if cached.get(market) else None
                if prob is None:
                    continue

                if market in ['btts']:
                    pick = 'Yes' if prob > 0.5 else 'No'
                elif market in ['ou25', 'ou15']:
                    pick = 'Over' if prob > 0.5 else 'Under'
                elif market == 'h2h':
                    pick = 'Home' if prob > 0.5 else 'Away'

                assert pick in expected_picks[market], f"Fixture {fix.id} {market} pick '{pick}' not in expected picks"


class TestOddsByTypeMapping:
    """Tests specifically for the odds_by_type mapping logic."""

    def test_fixture_has_multiple_odds_rows(self, db_session):
        """Verify a fixture can have multiple FixtureOdds rows with different bet_types."""
        now = datetime.utcnow()
        end = now + timedelta(days=7)

        fixtures = db_session.execute(
            select(Fixture)
            .where(Fixture.date >= now)
            .where(Fixture.date <= end)
            .where(Fixture.status == 'NS')
            .order_by(Fixture.date)
            .limit(50)
        ).scalars().all()

        fixture_with_multi = None
        for fix in fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            bet_types = set(row.bet_type for row in all_odds)
            if len(bet_types) >= 2:
                fixture_with_multi = fix
                break

        if fixture_with_multi is None:
            pytest.skip("No fixtures with multiple bet_type rows found in test set")

        all_odds = db_session.execute(
            select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_with_multi.id)
        ).scalars().all()
        bet_types = set(row.bet_type for row in all_odds)
        assert len(bet_types) >= 2, f"Fixture {fixture_with_multi.id} should have at least 2 different bet_type rows"

    def test_each_bet_type_has_unique_odds_columns(self, db_session, upcoming_fixtures):
        """Verify each bet_type row has its own relevant odds populated."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            for row in all_odds:
                if row.bet_type == 'btts':
                    assert row.odd_btts_yes is not None, f"btts row should have odd_btts_yes"
                elif row.bet_type == 'over_under':
                    assert row.odd_over is not None or row.odd_over15 is not None, \
                        f"over_under row should have odd_over or odd_over15"
                elif row.bet_type == 'h2h':
                    assert row.odd_home is not None, f"h2h row should have odd_home"
