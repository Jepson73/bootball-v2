"""
Decision Engine - Central risk-control layer for Bootball.

Subscribes to EventBus events and makes deterministic decisions
about system actions (alerts, retraining, throttling, etc.).
"""

import logging
from typing import Any, Callable, Optional

from src.alerts.event_bus import event_bus, Events
from src.decision_engine.state import DecisionState, get_decision_state
from src.decision_engine.rules import load_rules
from src.decision_engine.actions import Action, SEND_ALERT
from src.decision_engine.executors import ActionExecutor, get_action_executor
from src.decision_engine.registry import get_action_registry

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Central decision engine that evaluates rules against state
    and executes actions.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus or event_bus
        self.state = get_decision_state()  # Use global state
        self.rules = load_rules()
        self.executor = get_action_executor()  # Use action executor
        self.registry = get_action_registry()

        logger.info("DecisionEngine initialized")

    def handle_event(self, event_type: str, data: dict) -> None:
        """
        Handle incoming events from EventBus.
        
        Updates state and evaluates rules.
        """
        logger.debug(f"[DECISION] Received event: {event_type}")

        # Update state based on event type
        self._update_state(event_type, data)

        # Evaluate all rules
        for rule in self.rules:
            try:
                action = rule(self.state, event_type, data)
                if action:
                    logger.info(f"[DECISION] {action.type} -> {action.payload}")
                    self._execute(action)
            except Exception as e:
                logger.error(f"Rule {rule.__name__} failed: {e}")

    def _update_state(self, event_type: str, data: dict) -> None:
        """Update internal state based on event type."""
        if event_type == Events.HEALTH_UPDATE:
            self.state.update_health(data)

        elif event_type == Events.MODEL_TREND:
            self.state.update_trend(data)

        elif event_type in [Events.BET_SETTLED, Events.BETS_SETTLED]:
            # Handle both single and batch settlements
            if "bets" in data:
                for bet in data["bets"]:
                    self.state.record_settlement(bet)
            else:
                self.state.record_settlement(data)

        elif event_type == Events.RUN_STARTED:
            self.state.update_run_time()

    def _execute(self, action: Action) -> None:
        """Execute an action via the action executor."""
        try:
            # Record in registry
            self.registry.record(action.type, action.payload, success=True)
            # Execute via executor
            self.executor.execute(action)
        except Exception as e:
            logger.error(f"Action {action.type} failed: {e}")
            self.registry.record(action.type, action.payload, success=False, error=str(e))

    def get_state(self) -> DecisionState:
        """Get current decision state."""
        return self.state

    def force_decision(self, event_type: str, data: dict) -> list[Action]:
        """
        Force a decision evaluation without updating state.
        Returns list of actions that would be taken.
        """
        actions = []
        for rule in self.rules:
            try:
                action = rule(self.state, event_type, data)
                if action:
                    actions.append(action)
            except Exception as e:
                logger.error(f"Rule {rule.__name__} failed: {e}")
        return actions


# Global instance
_engine: Optional[DecisionEngine] = None


def get_decision_engine() -> DecisionEngine:
    """Get global decision engine instance."""
    global _engine
    if _engine is None:
        _engine = DecisionEngine(event_bus)
    return _engine


def start_decision_engine() -> DecisionEngine:
    """Start the decision engine and subscribe to events."""
    engine = get_decision_engine()

    # Subscribe to all relevant events.
    # event_bus calls handlers with a single event dict; unwrap before passing to handle_event.
    def _make_handler(et: str):
        def _handler(event: dict) -> None:
            engine.handle_event(et, event)
        return _handler

    for event_type in [
        Events.BETS_GENERATED,
        Events.BET_SETTLED,
        Events.BETS_SETTLED,
        Events.RUN_STARTED,
        Events.RUN_FINISHED,
        Events.HEALTH_UPDATE,
        Events.MODEL_TREND,
    ]:
        event_bus.subscribe(event_type, _make_handler(event_type))

    logger.info("DecisionEngine started and subscribed to events")
    return engine
