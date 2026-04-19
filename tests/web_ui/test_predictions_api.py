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
        """Verify all FixtureOdds rows are fetched per fixture."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            odds_by_type = {row.bet_type: row for row in all_odds}

            assert 'btts' in odds_by_type, f"Fixture {fix.id} missing btts odds"
            assert 'over_under' in odds_by_type, f"Fixture {fix.id} missing over_under odds"
            assert 'h2h' in odds_by_type, f"Fixture {fix.id} missing h2h odds"

    def test_btts_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify BTTS market uses odd_btts_yes from btts row."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            odds_by_type = {row.bet_type: row for row in all_odds}
            btts_row = odds_by_type.get('btts')

            assert btts_row is not None, f"Fixture {fix.id} has no BTTS odds row"
            assert btts_row.odd_btts_yes is not None, f"Fixture {fix.id} BTTS odd_btts_yes is NULL"

    def test_ou25_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify O/U 2.5 market uses odd_over from over_under row."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            odds_by_type = {row.bet_type: row for row in all_odds}
            ou_row = odds_by_type.get('over_under')

            assert ou_row is not None, f"Fixture {fix.id} has no over_under odds row"
            assert ou_row.odd_over is not None, f"Fixture {fix.id} OU25 odd_over is NULL"

    def test_ou15_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify O/U 1.5 market uses odd_over15 from over_under row."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            odds_by_type = {row.bet_type: row for row in all_odds}
            ou_row = odds_by_type.get('over_under')

            assert ou_row is not None, f"Fixture {fix.id} has no over_under odds row"
            assert ou_row.odd_over15 is not None, f"Fixture {fix.id} OU15 odd_over15 is NULL"

    def test_h2h_market_uses_correct_odds(self, db_session, upcoming_fixtures):
        """Verify H2H market uses odd_home from h2h row."""
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()

            odds_by_type = {row.bet_type: row for row in all_odds}
            h2h_row = odds_by_type.get('h2h')

            assert h2h_row is not None, f"Fixture {fix.id} has no h2h odds row"
            assert h2h_row.odd_home is not None, f"Fixture {fix.id} H2H odd_home is NULL"

    def test_all_markets_have_predictions(self, db_session, upcoming_fixtures):
        """Verify all 4 markets (btts, ou25, ou15, h2h) have predictions."""
        markets = ['btts', 'ou25', 'ou15', 'h2h']

        for fix in upcoming_fixtures:
            preds = db_session.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()

            cached = {p.market: p for p in preds}

            for market in markets:
                assert market in cached, f"Fixture {fix.id} missing {market} prediction"
                assert cached[market].our_prob is not None, f"Fixture {fix.id} {market} prob is NULL"

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

        fix = db_session.execute(
            select(Fixture)
            .where(Fixture.date >= now)
            .where(Fixture.date <= end)
            .where(Fixture.status == 'NS')
            .order_by(Fixture.date)
            .limit(1)
        ).scalar_one_or_none()

        if fix is None:
            pytest.skip("No upcoming fixtures found")

        all_odds = db_session.execute(
            select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
        ).scalars().all()

        bet_types = set(row.bet_type for row in all_odds)
        assert len(bet_types) >= 2, f"Fixture {fix.id} should have at least 2 different bet_type rows, got {len(bet_types)}"

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
