"""
Market Normalization Utility

Provides consistent market and pick normalization across the entire system.
Ensures that h2h, btts, ou25, ou15 markets use standardized pick values.

Usage:
    from src.prediction.market_normalizer import normalize_market, normalize_market_pick
    market = normalize_market("Over_Under")  # returns "ou25"
    pick = normalize_market_pick("h2h", "home")  # returns "1"
"""

import logging

logger = logging.getLogger(__name__)

VALID_MARKETS = ["h2h", "btts", "ou25", "ou15"]

MARKET_ENCODINGS = {
    "h2h": {
        "home": "1",
        "1": "1",
        "h": "1",
        "away": "2",
        "2": "2",
        "a": "2",
        "draw": "X",
        "x": "X",
        "d": "X",
    },
    "btts": {
        "yes": "Yes",
        "y": "Yes",
        "btts_yes": "Yes",
        "no": "No",
        "n": "No",
        "btts_no": "No",
    },
    "ou25": {
        "over": "Over",
        "o": "Over",
        "over25": "Over",
        "under": "Under",
        "u": "Under",
        "under25": "Under",
    },
    "ou15": {
        "over": "Over",
        "o": "Over",
        "over15": "Over",
        "under": "Under",
        "u": "Under",
        "under15": "Under",
    },
}


def normalize_market_pick(market: str, pick: str) -> str:
    """
    Normalize market pick to canonical form.
    
    Args:
        market: Market type (h2h, btts, ou25, ou15)
        pick: Raw pick value
        
    Returns:
        Normalized pick value
    """
    if not market or not pick:
        return ""
    
    normalized_market = normalize_market(market)
    encoding = MARKET_ENCODINGS.get(normalized_market, {})
    
    for key, normalized in encoding.items():
        if pick.lower() == key.lower():
            return normalized
    
    logger.warning(f"Unknown pick '{pick}' for market '{normalized_market}', returning as-is")
    return pick


def normalize_market(market: str) -> str:
    """
    Normalize market name to canonical form.
    
    Args:
        market: Raw market name
        
    Returns:
        Normalized market name
    """
    if not market:
        return ""
    
    market = market.lower().strip()
    
    if market in VALID_MARKETS:
        return market
    
    market_map = {
        "h2h": "h2h",
        "head2head": "h2h",
        "match_result": "h2h",
        "btts": "btts",
        "both_score": "btts",
        "bothteamsscore": "btts",
        "overunder": "ou25",
        "over_under": "ou25",
        "ou2.5": "ou25",
        "ou15": "ou15",
        "overunder15": "ou15",
        "ou1.5": "ou15",
    }
    
    normalized = market_map.get(market, market)
    if normalized != market:
        logger.debug(f"Normalized market '{market}' to '{normalized}'")
    
    return normalized


def get_pick_label(market: str, pick: str) -> str:
    """
    Get human-readable label for a pick.
    
    Args:
        market: Market type
        pick: Normalized pick
        
    Returns:
        Human-readable label
    """
    normalized_market = normalize_market(market)
    normalized_pick = normalize_market_pick(normalized_market, pick)
    
    labels = {
        "h2h": {"1": "Home Win", "X": "Draw", "2": "Away Win"},
        "btts": {"Yes": "Both Score", "No": "No Both Score"},
        "ou25": {"Over": "Over 2.5", "Under": "Under 2.5"},
        "ou15": {"Over": "Over 1.5", "Under": "Under 1.5"},
    }
    
    return labels.get(normalized_market, {}).get(normalized_pick, normalized_pick)


def is_valid_market(market: str) -> bool:
    """
    Check if market is valid.
    """
    return normalize_market(market) in VALID_MARKETS


def is_valid_pick(market: str, pick: str) -> bool:
    """
    Check if pick is valid for market.
    
    Args:
        market: Market type
        pick: Pick value
        
    Returns:
        True if valid
    """
    normalized_market = normalize_market(market)
    normalized_pick = normalize_market_pick(normalized_market, pick)
    return normalized_pick in get_valid_picks(normalized_market)


def get_valid_picks(market: str) -> list:
    """
    Get all valid picks for a market.
    """
    normalized_market = normalize_market(market)
    
    if normalized_market == "h2h":
        return ["1", "X", "2"]
    elif normalized_market == "btts":
        return ["Yes", "No"]
    elif normalized_market in ["ou25", "ou15"]:
        return ["Over", "Under"]
    
    return []
