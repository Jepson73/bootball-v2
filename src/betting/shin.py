"""
src/betting/shin.py

Shin (1993) method for removing bookmaker margin from raw odds.

Problem:
  Raw implied probs from odds sum to > 1.0 (the overround / vig).
  A naive 1/odd gives biased implied probs — underdogs are
  systematically overpriced relative to favourites.

Shin's method:
  Models the overround as arising from a fraction z of bettors
  who have inside information. Solves for the "true" probabilities
  that are consistent with the observed odds.

Why not just normalise?
  Simple normalisation (divide each implied prob by sum) biases
  towards the favourite. Shin's method is empirically more accurate,
  especially for H/D/A markets.

Reference:
  Shin, H.S. (1993) — Measuring the Incidence of Insider Trading
  in a Market for State-Contingent Claims. Economic Journal 103, 1141–1153.
  Also: Štrumbelj (2014) for practical football application.
"""
from __future__ import annotations
import numpy as np


def shin_probabilities(odds: list[float]) -> list[float]:
    """
    Convert decimal odds to true probabilities using Shin's method.

    Args:
        odds: List of decimal odds for mutually exclusive outcomes
              e.g. [2.10, 3.40, 3.80] for home/draw/away

    Returns:
        List of true probabilities summing to 1.0

    Example:
        >>> shin_probabilities([2.10, 3.40, 3.80])
        [0.462, 0.281, 0.257]   # approx
    """
    raw = np.array([1.0 / o for o in odds])
    overround = raw.sum()

    if abs(overround - 1.0) < 1e-6:
        return raw.tolist()

    # Solve for z (insider fraction) numerically
    # Shin equation: p_i = sqrt(z^2 + 4(1-z) * q_i/W) - z) / (2(1-z))
    # where q_i = 1/odd_i, W = sum(q_i)
    from scipy.optimize import brentq

    q = raw
    W = overround

    def objective(z: float) -> float:
        if z >= 1.0:
            return 1e10
        p = (np.sqrt(z**2 + 4 * (1 - z) * q / W) - z) / (2 * (1 - z))
        return p.sum() - 1.0

    try:
        z_star = brentq(objective, 0.0, 0.999, maxiter=200)
        p = (np.sqrt(z_star**2 + 4 * (1 - z_star) * q / W) - z_star) / (2 * (1 - z_star))
        # Normalise to clean up floating point
        p = p / p.sum()
        return p.tolist()
    except ValueError:
        # Fallback: simple normalisation
        return (raw / W).tolist()


def overround(odds: list[float]) -> float:
    """Returns the bookmaker margin as a percentage. E.g. 0.05 = 5% margin."""
    return sum(1.0 / o for o in odds) - 1.0
