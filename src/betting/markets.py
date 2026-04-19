"""
src/betting/markets.py

Betting market definitions. Each market has:
- name: Display name
- outcomes: Possible outcomes with labels
- prediction: Dictionary key for model predictions

Adding new markets:
1. Add entry to MARKETS dict
2. Create market-specific prediction model (src/models/)
3. Add odds fetching (market has its own bet_type ID)
4. Update web UI with new tab/section
"""
from dataclasses import dataclass
from enum import Enum


class BetMarket(Enum):
    H2H = "h2h"           # Match Winner (Home/Draw/Away)
    BTTS = "btts"          # Both Teams To Score (Yes/No)
    OU = "ou25"            # Over/Under 2.5 Goals (Over/Under)
    AH = "ah"              # Asian Handicap (later)
    CS = "cs"              # Correct Score (later)


@dataclass
class MarketDef:
    name: str
    outcomes: dict[str, str]      # code -> display label
    bet_type_id: int           # API-Football bet type ID
    description: str


MARKETS = {
    BetMarket.H2H: MarketDef(
        name="Match Winner",
        outcomes={"H": "Home", "D": "Draw", "A": "Away"},
        bet_type_id=1,
        description="Predict full time result",
    ),
    BetMarket.BTTS: MarketDef(
        name="Both Teams To Score",
        outcomes={"Y": "Yes", "N": "No"},
        bet_type_id=5,
        description="Will both teams score?",
    ),
    BetMarket.OU: MarketDef(
        name="Over/Under 2.5",
        outcomes={"O": "Over 2.5", "U": "Under 2.5"},
        bet_type_id=4,
        description="Total goals over/under 2.5",
    ),
}


def get_market(market: BetMarket) -> MarketDef:
    return MARKETS[market]