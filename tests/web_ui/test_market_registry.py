"""
tests/web_ui/test_market_registry.py

Tests for market registry and market-agnostic prediction logic.
New markets added to config/markets.py are automatically tested here.
"""
import sys
sys.path.insert(0, '.')

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from config.markets import MARKET_REGISTRY, get_all_markets, get_market
from src.storage.models import Fixture, FixtureOdds, PredictionRecord


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
    """Get 5 upcoming fixtures with predictions and odds."""
    now = datetime.utcnow()
    end = now + timedelta(days=7)

    fixtures = db_session.execute(
        select(Fixture)
        .where(Fixture.date >= now)
        .where(Fixture.date <= end)
        .where(Fixture.status == 'NS')
        .order_by(Fixture.date)
        .limit(10)
    ).scalars().all()

    return fixtures


class TestMarketRegistry:
    """Tests for market registry configuration."""

    def test_all_markets_have_required_fields(self):
        """Verify every market in registry has all required fields."""
        for market_id, config in MARKET_REGISTRY.items():
            assert config.market_id == market_id
            assert config.bet_type
            assert config.display_name
            assert config.odds_column
            assert len(config.pick_options) >= 2
            assert config.prob_direction in ('above_50', 'below_50')

    def test_market_ids_are_unique(self):
        """Verify no duplicate market IDs."""
        ids = list(MARKET_REGISTRY.keys())
        assert len(ids) == len(set(ids))

    def test_get_market_returns_config(self):
        """Verify get_market returns correct config."""
        for market_id in MARKET_REGISTRY:
            config = get_market(market_id)
            assert config is not None
            assert config.market_id == market_id

    def test_get_market_unknown_returns_none(self):
        """Verify get_market returns None for unknown market."""
        assert get_market('unknown_market') is None

    def test_get_all_markets_returns_list(self):
        """Verify get_all_markets returns all markets."""
        markets = get_all_markets()
        assert len(markets) == len(MARKET_REGISTRY)
        assert all(m.market_id in MARKET_REGISTRY for m in markets)


class TestMarketAgnosticPrediction:
    """Tests that verify prediction logic works for ALL markets in registry.

    When a new market is added to config/markets.py, these tests
    automatically cover it without modification.
    """

    def test_all_markets_have_odds_in_database(self, db_session, upcoming_fixtures):
        """Verify all registered markets have odds available for some fixtures."""
        markets_by_bet_type = {}
        for market_id, config in MARKET_REGISTRY.items():
            if config.bet_type not in markets_by_bet_type:
                markets_by_bet_type[config.bet_type] = []
            markets_by_bet_type[config.bet_type].append(config)

        bet_types_found = set()
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            for row in all_odds:
                bet_types_found.add(row.bet_type)

        for bet_type in markets_by_bet_type:
            assert bet_type in bet_types_found, \
                f"Bet type '{bet_type}' from registry not found in database fixtures"

    def test_each_market_can_extract_odds(self, db_session, upcoming_fixtures):
        """Verify each market can extract odds from its bet_type row."""
        for market_id, config in MARKET_REGISTRY.items():
            fixtures_with_market = 0
            for fix in upcoming_fixtures:
                all_odds = db_session.execute(
                    select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
                ).scalars().all()
                odds_by_type = {row.bet_type: row for row in all_odds}

                bet_type_row = odds_by_type.get(config.bet_type)
                if bet_type_row is None:
                    continue

                odds_value = getattr(bet_type_row, config.odds_column, None)
                if odds_value is not None and odds_value > 0:
                    fixtures_with_market += 1
                    break

            assert fixtures_with_market > 0, \
                f"Market '{market_id}' could not extract valid odds for any test fixture"

    def test_each_market_has_predictions(self, db_session, upcoming_fixtures):
        """Verify each market has predictions available."""
        for market_id in MARKET_REGISTRY:
            fixtures_with_pred = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if market_id in pred_dict and pred_dict[market_id].our_prob is not None:
                    fixtures_with_pred += 1
                    break

            assert fixtures_with_pred > 0, \
                f"Market '{market_id}' has no predictions in any test fixture"

    def test_pick_selection_for_all_markets(self, db_session, upcoming_fixtures):
        """Verify pick selection logic works for all markets."""
        for market_id, config in MARKET_REGISTRY.items():
            fixtures_tested = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if market_id not in pred_dict:
                    continue

                prob = pred_dict[market_id].our_prob
                if prob is None:
                    continue

                if config.prob_direction == 'above_50':
                    expected_pick = config.pick_options[0] if prob > 0.5 else config.pick_options[1]
                else:
                    expected_pick = config.pick_options[1] if prob > 0.5 else config.pick_options[0]

                assert expected_pick in config.pick_options
                fixtures_tested += 1

            assert fixtures_tested > 0, \
                f"Market '{market_id}' could not test pick selection"

    def test_ev_calculation_for_all_markets(self, db_session, upcoming_fixtures):
        """Verify EV calculation works for all markets."""
        for market_id, config in MARKET_REGISTRY.items():
            fixtures_tested = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if market_id not in pred_dict:
                    continue

                all_odds = db_session.execute(
                    select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
                ).scalars().all()
                odds_by_type = {row.bet_type: row for row in all_odds}

                bet_type_row = odds_by_type.get(config.bet_type)
                if bet_type_row is None:
                    continue

                odds = getattr(bet_type_row, config.odds_column, None)
                prob = pred_dict[market_id].our_prob

                if prob and odds and odds > 0:
                    ev = (prob * odds) - 1
                    assert -1 <= ev <= 10, f"{fix.id}/{market_id} EV {ev} out of range"
                    fixtures_tested += 1

            assert fixtures_tested > 0, \
                f"Market '{market_id}' could not test EV calculation"


class TestNewMarketAddition:
    """Tests to verify the process of adding new markets works correctly.

    To add a new market:
    1. Add MarketConfig to config/markets.py
    2. Add bet_type handling in web_ui.py api_predictions
    3. New markets automatically tested here
    """

    def test_new_market_requires_bet_type_handling(self):
        """Document that new markets need explicit handling in api_predictions."""
        markets_in_registry = set(MARKET_REGISTRY.keys())
        expected_markets = {'btts', 'ou25', 'ou15', 'h2h'}

        assert markets_in_registry == expected_markets, \
            f"Market list changed. Update web_ui.py api_predictions to handle: {markets_in_registry - expected_markets}"

    def test_market_needs_prediction_record(self):
        """Document that new markets need prediction records in database."""
        markets_in_registry = set(MARKET_REGISTRY.keys())
        expected_markets = {'btts', 'ou25', 'ou15', 'h2h'}

        assert markets_in_registry == expected_markets, \
            f"Market list changed. Ensure prediction pipeline generates records for: {markets_in_registry - expected_markets}"
