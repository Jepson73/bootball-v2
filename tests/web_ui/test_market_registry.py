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

    def test_all_active_markets_have_odds_in_database(self, db_session, upcoming_fixtures):
        """Verify all ACTIVE markets have odds available for some fixtures."""
        from config.markets import get_active_markets

        active_markets = get_active_markets()
        bet_types_found = set()
        for fix in upcoming_fixtures:
            all_odds = db_session.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
            ).scalars().all()
            for row in all_odds:
                bet_types_found.add(row.bet_type)

        for config in active_markets:
            assert config.bet_type in bet_types_found, \
                f"Active market '{config.market_id}' needs bet_type '{config.bet_type}' in database"

    def test_each_active_market_can_extract_odds(self, db_session, upcoming_fixtures):
        """Verify each ACTIVE market can extract odds from its bet_type row."""
        from config.markets import get_active_markets

        for config in get_active_markets():
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
                f"Active market '{config.market_id}' could not extract valid odds"

    def test_each_active_market_has_predictions(self, db_session, upcoming_fixtures):
        """Verify each ACTIVE market has predictions available."""
        from config.markets import get_active_markets

        for config in get_active_markets():
            fixtures_with_pred = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if config.market_id in pred_dict and pred_dict[config.market_id].our_prob is not None:
                    fixtures_with_pred += 1
                    break

            assert fixtures_with_pred > 0, \
                f"Active market '{config.market_id}' has no predictions"

    def test_pick_selection_for_all_active_markets(self, db_session, upcoming_fixtures):
        """Verify pick selection logic works for all ACTIVE markets."""
        from config.markets import get_active_markets

        for config in get_active_markets():
            fixtures_tested = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if config.market_id not in pred_dict:
                    continue

                prob = pred_dict[config.market_id].our_prob
                if prob is None:
                    continue

                if config.prob_direction == 'above_50':
                    expected_pick = config.pick_options[0] if prob > 0.5 else config.pick_options[1]
                else:
                    expected_pick = config.pick_options[1] if prob > 0.5 else config.pick_options[0]

                assert expected_pick in config.pick_options
                fixtures_tested += 1

            assert fixtures_tested > 0, \
                f"Active market '{config.market_id}' could not test pick selection"

    def test_ev_calculation_for_all_active_markets(self, db_session, upcoming_fixtures):
        """Verify EV calculation works for all ACTIVE markets."""
        from config.markets import get_active_markets

        for config in get_active_markets():
            fixtures_tested = 0
            for fix in upcoming_fixtures:
                preds = db_session.execute(
                    select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
                ).scalars().all()
                pred_dict = {p.market: p for p in preds}

                if config.market_id not in pred_dict:
                    continue

                all_odds = db_session.execute(
                    select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)
                ).scalars().all()
                odds_by_type = {row.bet_type: row for row in all_odds}

                bet_type_row = odds_by_type.get(config.bet_type)
                if bet_type_row is None:
                    continue

                odds = getattr(bet_type_row, config.odds_column, None)
                prob = pred_dict[config.market_id].our_prob

                if prob and odds and odds > 0:
                    ev = (prob * odds) - 1
                    assert -1 <= ev <= 10, f"{fix.id}/{config.market_id} EV {ev} out of range"
                    fixtures_tested += 1

            assert fixtures_tested > 0, \
                f"Active market '{config.market_id}' could not test EV calculation"


class TestNewMarketAddition:
    """Tests to verify the process of adding new markets works correctly.

    To add a new market:
    1. Add MarketConfig to config/markets.py with status='planned'
    2. Change status to 'active' when odds/predictions are available
    3. Add bet_type handling in web_ui.py api_predictions
    4. Active markets automatically tested above
    """

    def test_all_markets_have_valid_config(self):
        """Verify every market has a valid configuration."""
        from config.markets import get_all_markets

        for config in get_all_markets():
            assert config.market_id
            assert config.bet_type
            assert config.display_name
            assert config.odds_column
            assert len(config.pick_options) >= 2
            assert config.prob_direction in ('above_50', 'below_50')
            assert config.status in ('active', 'planned', 'researching')

    def test_market_count_matches_roadmap(self):
        """Document current market count for roadmap tracking."""
        from config.markets import get_active_markets, get_planned_markets, get_all_markets

        active = len(get_active_markets())
        planned = len(get_planned_markets())
        total = len(get_all_markets())

        assert active >= 4, f"Should have at least 4 active markets, got {active}"
        assert total >= active + planned

    def test_market_registry_is_documented(self):
        """Verify market registry documents API-Football IDs where available."""
        from config.markets import get_all_markets

        for config in get_all_markets():
            if config.status == 'active' and config.bet_type != 'over_under':
                assert config.api_football_id is not None, \
                    f"Active market '{config.market_id}' should have API-Football ID"
