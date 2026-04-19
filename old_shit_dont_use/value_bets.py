"""
src/betting/value_bets.py

Identifies value bets by comparing our model probabilities
against bookmaker odds (after removing margin via Shin method).

Supports all markets: h2h, btts, ou25, ou15

Pipeline:
  1. Get our model's probabilities for a market
  2. Get best available odds from DB (FixtureOdds)
  3. Apply Shin method to get true implied probabilities
  4. Compute EV for each outcome
  5. Flag bets where EV > threshold
  6. Size bets with fractional Kelly
  7. Write to ValueBets table
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.betting.ev import expected_value
from src.betting.kelly import fractional_kelly
from src.betting.shin import shin_probabilities
from src.betting.predict import predict_proba, get_market_outcomes

logger = logging.getLogger(__name__)

EV_THRESHOLD = 0.05      # Only bet if EV > 5%
KELLY_FRACTION = 0.25    # Quarter-Kelly
MAX_STAKE_PCT = 0.05     # Never bet more than 5% of bankroll on one game


@dataclass
class ValueBetCandidate:
    fixture_id: int
    market: str              # h2h, btts, ou25, ou15
    outcome: str             # H/D/A, Yes/No, Over/Under
    our_prob: float
    bookmaker: str
    decimal_odd: float
    implied_prob_raw: float  # 1/odd (includes margin)
    implied_prob_shin: float # after Shin correction
    ev: float               # Expected Value as fraction
    kelly_fraction: float


ODDS_FIELD_MAP = {
    "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
}


def get_odds_for_market(odds_row, market: str) -> dict[str, float]:
    """Extract odds for a specific market from a FixtureOdds row."""
    field_map = ODDS_FIELD_MAP.get(market, {})
    odds = {}
    for outcome, field in field_map.items():
        value = getattr(odds_row, field, None)
        if value:
            odds[outcome] = value
    return odds


def find_value_bets(
    fixture_id: int,
    market: str,
    model_probs: dict[str, float],
    odds_row,
    ev_threshold: float = EV_THRESHOLD,
) -> list[ValueBetCandidate]:
    """
    Returns all value bet candidates for a fixture and market.

    Args:
        fixture_id: API-Football fixture ID
        market: One of "h2h", "btts", "ou25", "ou15"
        model_probs: Our predicted probabilities for this market
        odds_row: FixtureOdds ORM object for this fixture
        ev_threshold: Minimum EV to flag as a value bet

    Returns:
        Sorted list of ValueBetCandidate by EV descending
    """
    candidates: list[ValueBetCandidate] = []

    market_odds = get_odds_for_market(odds_row, market)
    if not market_odds:
        return candidates

    outcomes = list(market_odds.keys())
    odds_values = [market_odds[o] for o in outcomes]

    if len(odds_values) < 2:
        return candidates

    try:
        shin_probs = shin_probabilities(odds_values)
    except Exception:
        logger.warning(f"Shin method failed for fixture {fixture_id}, using raw implied")
        shin_probs = [1 / o for o in odds_values]

    for i, outcome in enumerate(outcomes):
        model_prob = model_probs.get(outcome, 0.0)
        decimal_odd = market_odds[outcome]

        if decimal_odd <= 0:
            continue

        ev = expected_value(model_prob, decimal_odd)
        if ev < ev_threshold:
            continue

        kf = fractional_kelly(model_prob, decimal_odd, KELLY_FRACTION)
        implied_raw = 1.0 / decimal_odd
        shin_implied = shin_probs[i] if i < len(shin_probs) else implied_raw

        candidates.append(ValueBetCandidate(
            fixture_id=fixture_id,
            market=market,
            outcome=outcome,
            our_prob=model_prob,
            bookmaker=odds_row.bookmaker,
            decimal_odd=decimal_odd,
            implied_prob_raw=implied_raw,
            implied_prob_shin=shin_implied,
            ev=ev,
            kelly_fraction=kf,
        ))

    candidates.sort(key=lambda x: x.ev, reverse=True)
    return candidates


def find_value_bets_for_fixture(
    fixture_id: int,
    market: str,
    odds_row,
    ev_threshold: float = EV_THRESHOLD,
) -> list[ValueBetCandidate]:
    """
    Convenience function: predict and find value bets in one call.

    Args:
        fixture_id: API-Football fixture ID
        market: One of "h2h", "btts", "ou25", "ou15"
        odds_row: FixtureOdds ORM object
        ev_threshold: Minimum EV threshold

    Returns:
        List of ValueBetCandidate sorted by EV descending
    """
    home_id = odds_row.fixture.home_team_id if odds_row.fixture else None
    away_id = odds_row.fixture.away_team_id if odds_row.fixture else None

    if not home_id or not away_id:
        return []

    model_probs = predict_proba(market, home_id, away_id)
    return find_value_bets(
        fixture_id=fixture_id,
        market=market,
        model_probs=model_probs,
        odds_row=odds_row,
        ev_threshold=ev_threshold,
    )


def find_all_market_value_bets(
    fixture_id: int,
    home_id: int,
    away_id: int,
    odds_row,
    markets: list[str] | None = None,
    ev_threshold: float = EV_THRESHOLD,
) -> list[ValueBetCandidate]:
    """
    Find value bets across all requested markets for a fixture.

    Args:
        fixture_id: API-Football fixture ID
        home_id: Home team ID
        away_id: Away team ID
        odds_row: FixtureOdds ORM object
        markets: List of markets to check (default: all)
        ev_threshold: Minimum EV threshold

    Returns:
        All value bet candidates across all markets, sorted by EV descending
    """
    if markets is None:
        markets = ["h2h", "btts", "ou25", "ou15"]

    all_candidates = []

    for market in markets:
        try:
            model_probs = predict_proba(market, home_id, away_id)
            candidates = find_value_bets(
                fixture_id=fixture_id,
                market=market,
                model_probs=model_probs,
                odds_row=odds_row,
                ev_threshold=ev_threshold,
            )
            all_candidates.extend(candidates)
        except Exception as e:
            logger.warning(f"Error finding value bets for {market}: {e}")

    all_candidates.sort(key=lambda x: x.ev, reverse=True)
    return all_candidates


def best_odds(odds_row, market: str, outcome: str) -> tuple[float, str] | None:
    """
    Return the best available decimal odd for a given outcome.

    Args:
        odds_row: FixtureOdds ORM object
        market: h2h, btts, ou25, ou15
        outcome: Specific outcome (e.g., "Yes", "Over", "1")

    Returns:
        (decimal_odd, bookmaker_name) or None
    """
    market_odds = get_odds_for_market(odds_row, market)
    odd = market_odds.get(outcome)
    if odd:
        return odd, odds_row.bookmaker
    return None
