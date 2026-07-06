"""
Decision Engine - Central risk-control layer.

Subscribes to EventBus events and makes deterministic decisions
about system actions (alerts, retraining, throttling, etc.).

Usage:
    from src.decision_engine import start_decision_engine
    
    # Start the engine
    engine = start_decision_engine()
"""

from src.decision_engine.engine import DecisionEngine, get_decision_engine, start_decision_engine
from src.decision_engine.state import DecisionState, get_decision_state
from src.decision_engine.rules import load_rules
from src.decision_engine.actions import (
    Action,
    RETRAIN_MODEL,
    DISABLE_MARKET,
    THROTTLE_BETTING,
    INCREASE_CONFIDENCE,
    ALERT_ONLY_MODE,
    SEND_ALERT,
    RESET_THROTTLE,
    REENABLE_MARKET,
)
from src.decision_engine.executors import ActionExecutor, get_action_executor
from src.decision_engine.registry import ActionRegistry, get_action_registry

__all__ = [
    "DecisionEngine",
    "get_decision_engine",
    "start_decision_engine",
    "DecisionState",
    "get_decision_state",
    "load_rules",
    "Action",
    "RETRAIN_MODEL",
    "DISABLE_MARKET",
    "THROTTLE_BETTING",
    "INCREASE_CONFIDENCE",
    "ALERT_ONLY_MODE",
    "SEND_ALERT",
    "RESET_THROTTLE",
    "REENABLE_MARKET",
    "ActionExecutor",
    "get_action_executor",
    "ActionRegistry",
    "get_action_registry",
]
