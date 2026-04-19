"""
config/markets.py

Market registry defining all supported betting markets.
Add new markets here to have them automatically covered by tests.
"""
from dataclasses import dataclass


@dataclass
class MarketConfig:
    """Configuration for a betting market."""
    market_id: str          # Unique identifier (e.g., 'btts', 'h2h')
    bet_type: str           # Database bet_type value (e.g., 'btts', 'over_under', 'h2h')
    display_name: str       # Human-readable name
    odds_column: str        # Which column has the primary odds
    pick_options: tuple    # Possible pick values
    prob_direction: str     # 'above_50' means prob > 0.5 indicates first pick


MARKET_REGISTRY = {
    'btts': MarketConfig(
        market_id='btts',
        bet_type='btts',
        display_name='Both Teams To Score',
        odds_column='odd_btts_yes',
        pick_options=('Yes', 'No'),
        prob_direction='above_50',
    ),
    'ou25': MarketConfig(
        market_id='ou25',
        bet_type='over_under',
        display_name='Over/Under 2.5',
        odds_column='odd_over',
        pick_options=('Over', 'Under'),
        prob_direction='above_50',
    ),
    'ou15': MarketConfig(
        market_id='ou15',
        bet_type='over_under',
        display_name='Over/Under 1.5',
        odds_column='odd_over15',
        pick_options=('Over', 'Under'),
        prob_direction='above_50',
    ),
    'h2h': MarketConfig(
        market_id='h2h',
        bet_type='h2h',
        display_name='Head to Head',
        odds_column='odd_home',
        pick_options=('Home', 'Away'),
        prob_direction='above_50',
    ),
}


def get_all_markets():
    """Return list of all market configs."""
    return list(MARKET_REGISTRY.values())


def get_market(market_id: str) -> MarketConfig | None:
    """Get config for a specific market."""
    return MARKET_REGISTRY.get(market_id)


def get_markets_by_bet_type(bet_type: str) -> list[MarketConfig]:
    """Get all markets that use a specific bet_type."""
    return [m for m in MARKET_REGISTRY.values() if m.bet_type == bet_type]
