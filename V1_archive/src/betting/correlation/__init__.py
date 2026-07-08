"""
Cross-Market Correlation Risk Layer.

Ensures bets are not evaluated in isolation but adjusted
based on shared underlying match outcomes.
"""

from src.betting.correlation.correlation_engine import (
    CorrelationEngine,
    get_correlation_engine,
    DEFAULT_CORRELATION,
)

__all__ = [
    "CorrelationEngine",
    "get_correlation_engine",
    "DEFAULT_CORRELATION",
]
