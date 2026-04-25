"""
Backtesting module - historical simulation engine.

Provides:
- BacktestEngine: Replay events with strategy modifications
- Scenarios: Predefined strategy configurations
- Comparator: A/B testing of strategies
"""

from src.backtesting.backtest_engine import (
    BacktestEngine,
    run_backtest,
    run_scenario,
    DEFAULT_CONFIG,
)
from src.backtesting.scenarios import (
    ScenarioBuilder,
    get_scenario,
    list_scenarios,
    compare_scenarios,
    SCENARIOS,
)
from src.backtesting.comparator import (
    BacktestComparator,
    compare_scenarios,
    rank_strategies,
    risk_adjusted_return,
)

__all__ = [
    "BacktestEngine",
    "run_backtest",
    "run_scenario",
    "DEFAULT_CONFIG",
    "ScenarioBuilder",
    "get_scenario",
    "list_scenarios",
    "compare_scenarios",
    "SCENARIOS",
    "BacktestComparator",
    "rank_strategies",
    "risk_adjusted_return",
]
