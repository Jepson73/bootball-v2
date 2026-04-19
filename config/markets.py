"""
config/markets.py

Market registry defining all supported and planned betting markets.
Add new markets here to have them automatically covered by tests.

API-Football bet_type IDs:
    1 = Match Winner (1X2)
    3 = Asian Handicap
    4 = Over/Under 2.5
    5 = BTTS
    6 = Correct Score
    7 = Half Time/Full Time
    12 = Goal Intervals
    14 = BTTS 1st Half
    15 = BTTS 2nd Half
"""
from dataclasses import dataclass


@dataclass
class MarketConfig:
    """Configuration for a betting market."""
    market_id: str           # Unique identifier (e.g., 'btts', 'h2h')
    bet_type: str            # Database bet_type value
    api_football_id: int | None  # API-Football bet_type ID
    display_name: str        # Human-readable name
    description: str        # Brief description
    odds_column: str         # Which column has the primary odds
    secondary_odds: list[str]  # Other relevant odds columns
    pick_options: tuple     # Possible pick values
    prob_direction: str     # 'above_50' means prob > 0.5 indicates first pick
    status: str              # 'active', 'planned', 'researching'


MARKET_REGISTRY = {

    # === CURRENT ACTIVE MARKETS ===

    'btts': MarketConfig(
        market_id='btts',
        bet_type='btts',
        api_football_id=5,
        display_name='Both Teams To Score',
        description='Both teams to score at least one goal',
        odds_column='odd_btts_yes',
        secondary_odds=['odd_btts_no'],
        pick_options=('Yes', 'No'),
        prob_direction='above_50',
        status='active',
    ),

    'ou25': MarketConfig(
        market_id='ou25',
        bet_type='over_under',
        api_football_id=4,
        display_name='Over/Under 2.5',
        description='Total goals over or under 2.5',
        odds_column='odd_over',
        secondary_odds=['odd_under'],
        pick_options=('Over', 'Under'),
        prob_direction='above_50',
        status='active',
    ),

    'ou15': MarketConfig(
        market_id='ou15',
        bet_type='over_under',
        api_football_id=None,  # API may not distinguish 1.5 vs 2.5
        display_name='Over/Under 1.5',
        description='Total goals over or under 1.5',
        odds_column='odd_over15',
        secondary_odds=['odd_under15'],
        pick_options=('Over', 'Under'),
        prob_direction='above_50',
        status='active',
    ),

    'h2h': MarketConfig(
        market_id='h2h',
        bet_type='h2h',
        api_football_id=1,
        display_name='Match Winner (1X2)',
        description='Home win, draw, or away win',
        odds_column='odd_home',
        secondary_odds=['odd_draw', 'odd_away'],
        pick_options=('Home', 'Draw', 'Away'),
        prob_direction='above_50',
        status='active',
    ),

    # === PLANNED MARKETS ===

    'ah': MarketConfig(
        market_id='ah',
        bet_type='asian_handicap',
        api_football_id=3,
        display_name='Asian Handicap',
        description='Handicap with half-ball and quarter-ball lines',
        odds_column='odd_home',
        secondary_odds=[],
        pick_options=('Home', 'Away'),
        prob_direction='above_50',
        status='planned',
    ),

    'cs': MarketConfig(
        market_id='cs',
        bet_type='correct_score',
        api_football_id=6,
        display_name='Correct Score',
        description='Exact final score (e.g., 2-1, 3-0)',
        odds_column='odd_home',
        secondary_odds=[],
        pick_options=('Home', 'Draw', 'Away'),  # Simplified - actual CS is more complex
        prob_direction='above_50',
        status='planned',
    ),

    'htft': MarketConfig(
        market_id='htft',
        bet_type='half_time_full_time',
        api_football_id=7,
        display_name='Half Time/Full Time',
        description='Result at halftime AND fulltime (e.g., Home/Home, Draw/Away)',
        odds_column='odd_home',
        secondary_odds=[],
        pick_options=('Home/Home', 'Home/Draw', 'Home/Away', 'Draw/Home', 'Draw/Draw', 'Draw/Away', 'Away/Home', 'Away/Draw', 'Away/Away'),
        prob_direction='above_50',
        status='planned',
    ),

    'btts_1h': MarketConfig(
        market_id='btts_1h',
        bet_type='btts_first_half',
        api_football_id=14,
        display_name='BTTS 1st Half',
        description='Both teams score in first half',
        odds_column='odd_btts_yes',
        secondary_odds=[],
        pick_options=('Yes', 'No'),
        prob_direction='above_50',
        status='researching',
    ),

    'btts_2h': MarketConfig(
        market_id='btts_2h',
        bet_type='btts_second_half',
        api_football_id=15,
        display_name='BTTS 2nd Half',
        description='Both teams score in second half',
        odds_column='odd_btts_yes',
        secondary_odds=[],
        pick_options=('Yes', 'No'),
        prob_direction='above_50',
        status='researching',
    ),

    'dc': MarketConfig(
        market_id='dc',
        bet_type='double_chance',
        api_football_id=None,
        display_name='Double Chance',
        description='Two of three possible outcomes (1X, X2, 12)',
        odds_column='odd_home',
        secondary_odds=['odd_away'],
        pick_options=('Home or Draw', 'Draw or Away', 'Home or Away'),
        prob_direction='above_50',
        status='planned',
    ),

    'dnbtts': MarketConfig(
        market_id='dnbtts',
        bet_type='draw_no_bet',
        api_football_id=None,
        display_name='Draw No Bet',
        description='If draw, stake refunded',
        odds_column='odd_home',
        secondary_odds=['odd_away'],
        pick_options=('Home', 'Away'),
        prob_direction='above_50',
        status='planned',
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


def get_active_markets():
    """Get all markets with status='active'."""
    return [m for m in MARKET_REGISTRY.values() if m.status == 'active']


def get_planned_markets():
    """Get all markets with status='planned' or 'researching'."""
    return [m for m in MARKET_REGISTRY.values() if m.status in ('planned', 'researching')]
