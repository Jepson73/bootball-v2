"""
Portfolio Optimization using Markowitz Mean-Variance.

Replaces heuristic allocation with covariance-aware capital allocation.
"""

from src.betting.portfolio.markowitz_optimizer import (
    MarkowitzOptimizer,
    BetCandidate,
    OptimizationResult,
    get_markowitz_optimizer,
)

__all__ = [
    "MarkowitzOptimizer",
    "BetCandidate",
    "OptimizationResult",
    "get_markowitz_optimizer",
]
