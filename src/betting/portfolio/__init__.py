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
from src.betting.portfolio.portfolio_engine import (
    PortfolioEngine,
    PortfolioConfig,
    AllocationVector,
    get_portfolio_engine,
)

__all__ = [
    "MarkowitzOptimizer",
    "BetCandidate",
    "OptimizationResult",
    "get_markowitz_optimizer",
    "PortfolioEngine",
    "PortfolioConfig",
    "AllocationVector",
    "get_portfolio_engine",
]
