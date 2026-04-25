"""
State builders for dashboards.

These provide a clean interface between events and UI:
- No SQL in UI layer
- All state derived from events
- Deterministic reconstruction
"""

from src.state.builders.betting_state_builder import (
    build_betting_state,
    get_current_balance,
    get_pending_bets,
    get_settled_bets,
)
from src.state.builders.health_state_builder import (
    build_health_state,
    get_system_health_score,
    get_error_rate,
    get_active_runs,
    get_run_history,
)
from src.state.builders.model_state_builder import (
    build_model_state,
    get_market_performance,
    get_calibration_drift,
    get_active_model_versions,
    get_retrain_signals,
)

__all__ = [
    "build_betting_state",
    "build_health_state", 
    "build_model_state",
    "get_current_balance",
    "get_pending_bets",
    "get_settled_bets",
    "get_system_health_score",
    "get_error_rate",
    "get_active_runs",
    "get_run_history",
    "get_market_performance",
    "get_calibration_drift",
    "get_active_model_versions",
    "get_retrain_signals",
]
