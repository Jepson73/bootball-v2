"""
src/calibration/market_blend.py

Shrinks the model's probability toward the bookmaker's de-vigged
(Shin) market-implied probability before it drives EV/Kelly.

Why: investigation (2026-06) found the bookmaker's odds-implied
probability sits within ~1pt of the actual outcome rate in every
market the system bets on, while the model's own "calibrated"
probability runs 10-30pts overconfident — always in the same
direction. Per Hubáček, Šourek & Železný (2019), an accurate-but-
correlated model still loses to the vig; profit requires the model
to disagree with the market AND be right more often than the market
when it does. Standings-only features carry no information the
market doesn't already have, so most of that "disagreement" is model
error, not edge — and it was empirically the highest-EV bets that
performed worst. Blending pulls EV/Kelly back toward the market's
much-better-calibrated view without discarding the model entirely.
"""
from __future__ import annotations

from typing import Optional

from src.prediction.lib.shin import shin_probabilities

# Weight given to the model's own probability; the rest goes to the
# de-vigged market-implied probability. Deliberately model-skeptical:
# the market was shown to be far closer to the true outcome rate than
# the model in every market currently used.
MODEL_WEIGHT = 0.35


def blend_with_market(
    p_model: float,
    market_odds: dict[str, float],
    outcome: str,
) -> tuple[float, Optional[float]]:
    """Blend a model probability toward the market-implied probability.

    Args:
        p_model: model's (calibrated) probability for `outcome`.
        market_odds: decimal odds for ALL mutually exclusive outcomes of
            the market, e.g. {"1": 2.1, "X": 3.4, "2": 3.8} — Shin's
            method needs the full set to remove the overround.
        outcome: the outcome label whose blended probability to return.

    Returns:
        (p_blended, p_market). p_market is None (and p_blended == p_model)
        when the odds set is unusable (missing outcome, bad odds, etc.).
    """
    labels = list(market_odds.keys())
    odds = [market_odds[label] for label in labels]

    if len(odds) < 2 or outcome not in labels or any(o is None or o < 1.01 for o in odds):
        return p_model, None

    try:
        devigged = shin_probabilities(odds)
    except Exception:
        return p_model, None

    p_market = devigged[labels.index(outcome)]
    p_blended = MODEL_WEIGHT * p_model + (1 - MODEL_WEIGHT) * p_market
    return p_blended, p_market
