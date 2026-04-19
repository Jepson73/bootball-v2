"""
src/betting/__init__.py

Betting module exports.
"""
from src.betting.ev import expected_value, implied_probability
from src.betting.kelly import kelly_fraction, fractional_kelly
from src.betting.shin import shin_probabilities, overround
from src.betting.predict import predict_proba, get_market_outcomes
from src.betting.value_bets import (
    ValueBetCandidate,
    find_value_bets,
    find_all_market_value_bets,
    best_odds,
    EV_THRESHOLD,
    KELLY_FRACTION,
    MAX_STAKE_PCT,
)

__all__ = [
    "expected_value",
    "implied_probability",
    "kelly_fraction",
    "fractional_kelly",
    "shin_probabilities",
    "overround",
    "predict_proba",
    "get_market_outcomes",
    "ValueBetCandidate",
    "find_value_bets",
    "find_all_market_value_bets",
    "best_odds",
    "EV_THRESHOLD",
    "KELLY_FRACTION",
    "MAX_STAKE_PCT",
]
