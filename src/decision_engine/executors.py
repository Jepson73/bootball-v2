"""
Action Executors - Map actions to real system behavior.

Each executor handles a specific action type and performs
real operations (retraining, disabling markets, etc.).
"""

import logging
from typing import Any, Callable

from src.decision_engine.actions import Action
from src.alerts.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Executes actions by calling real system handlers."""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus or event_bus
        self._handlers: dict[str, Callable] = {}
        self._register_default_handlers()
        
        logger.info("ActionExecutor initialized")

    def _register_default_handlers(self) -> None:
        """Register default handlers for all action types."""
        self.register_handler("RETRAIN_MODEL", self._handle_retrain_model)
        self.register_handler("DISABLE_MARKET", self._handle_disable_market)
        self.register_handler("REENABLE_MARKET", self._handle_reenable_market)
        self.register_handler("THROTTLE_BETTING", self._handle_throttle)
        self.register_handler("RESET_THROTTLE", self._handle_reset_throttle)
        self.register_handler("INCREASE_CONFIDENCE", self._handle_increase_confidence)
        self.register_handler("ALERT_ONLY_MODE", self._handle_alert_only_mode)
        self.register_handler("SEND_ALERT", self._handle_send_alert)

    def register_handler(self, action_type: str, handler: Callable) -> None:
        """Register a handler for an action type."""
        self._handlers[action_type] = handler
        logger.debug(f"Registered handler for: {action_type}")

    def execute(self, action: Action) -> None:
        """Execute an action via its handler."""
        handler = self._handlers.get(action.type)
        if handler:
            try:
                logger.warning(f"[ACTION] {action.type} -> {action.payload}")
                handler(action.payload)
            except Exception as e:
                logger.error(f"Action {action.type} failed: {e}")
        else:
            logger.warning(f"No handler for action: {action.type}")

    def _handle_retrain_model(self, payload: dict) -> None:
        """Execute model retraining."""
        market = payload.get("market")
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing RETRAIN_MODEL for market: {market}")
        
        # Emit retrain started event
        self.event_bus.emit(Events.RUN_STARTED, {
            "run_id": f"retrain-{market}",
            "mode": "retrain",
            "market": market,
            "reason": reason,
            "timestamp": None
        })
        
        # Trigger actual retraining
        try:
            from src.models.retrain_worker import get_retrain_worker
            worker = get_retrain_worker()
            job_id = worker.queue_retrain(market, {
                "trigger": "decision_engine",
                "reasons": [reason],
                "context": payload.get("context", {})
            })
            logger.info(f"Retrain job queued: {job_id}")
        except Exception as e:
            logger.error(f"Retrain failed: {e}")
        
        # Emit retrain finished event
        self.event_bus.emit(Events.RUN_FINISHED, {
            "run_id": f"retrain-{market}",
            "mode": "retrain",
            "market": market,
            "total_bets": 0,
            "total_ev": 0,
            "errors": [],
            "duration": 0
        })

    def _handle_disable_market(self, payload: dict) -> None:
        """Disable a betting market."""
        market = payload.get("market")
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing DISABLE_MARKET: {market}")
        
        # Update global state
        from src.decision_engine.state import get_decision_state
        state = get_decision_state()
        state.disable_market(market)
        
        # Emit alert
        self.event_bus.emit(Events.ALERT_TRIGGERED, {
            "title": f"Market Disabled: {market}",
            "message": reason,
            "severity": "warning"
        })

    def _handle_reenable_market(self, payload: dict) -> None:
        """Re-enable a previously disabled market."""
        market = payload.get("market")
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing REENABLE_MARKET: {market}")
        
        # Update global state
        from src.decision_engine.state import get_decision_state
        state = get_decision_state()
        state.enable_market(market)
        
        # Emit alert
        self.event_bus.emit(Events.ALERT_TRIGGERED, {
            "title": f"Market Re-enabled: {market}",
            "message": reason,
            "severity": "success"
        })

    def _handle_throttle(self, payload: dict) -> None:
        """Throttle betting (reduce bet frequency)."""
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing THROTTLE_BETTING: {reason}")
        
        # Update global state
        from src.decision_engine.state import get_decision_state
        state = get_decision_state()
        state.activate_throttle()
        
        # Emit alert
        self.event_bus.emit(Events.ALERT_TRIGGERED, {
            "title": "Betting Throttled",
            "message": reason,
            "severity": "warning"
        })

    def _handle_reset_throttle(self, payload: dict) -> None:
        """Reset throttling to normal."""
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing RESET_THROTTLE: {reason}")
        
        # Update global state
        from src.decision_engine.state import get_decision_state
        state = get_decision_state()
        state.deactivate_throttle()
        
        # Emit alert
        self.event_bus.emit(Events.ALERT_TRIGGERED, {
            "title": "Betting Resumed Normal",
            "message": reason,
            "severity": "success"
        })

    def _handle_increase_confidence(self, payload: dict) -> None:
        """Log increase confidence (informational for now)."""
        market = payload.get("market")
        roi = payload.get("roi", 0)
        
        logger.info(f"Executing INCREASE_CONFIDENCE for {market}: ROI={roi}%")
        
        # For now, just log - could increase bet size in future

    def _handle_alert_only_mode(self, payload: dict) -> None:
        """Enable alert-only mode (disable all betting)."""
        reason = payload.get("reason", "decision_engine")
        
        logger.info(f"Executing ALERT_ONLY_MODE: {reason}")
        
        # Update global state
        from src.decision_engine.state import get_decision_state
        state = get_decision_state()
        state.set_alert_only_mode(True)
        
        # Emit alert
        self.event_bus.emit(Events.ALERT_TRIGGERED, {
            "title": "Alert-Only Mode Enabled",
            "message": reason,
            "severity": "error"
        })

    def _handle_send_alert(self, payload: dict) -> None:
        """Route alert through EventBus."""
        title = payload.get("title", "Alert")
        message = payload.get("message", "")
        severity = payload.get("severity", "warning")
        
        logger.info(f"Executing SEND_ALERT: {title}")
        
        # Emit to EventBus (DiscordConsumer will pick this up)
        self.event_bus.emit(Events.NOTIFICATION_DISCORD, {
            "title": title,
            "description": message,
            "severity": severity
        })


# Global executor instance
_executor: ActionExecutor = None


def get_action_executor() -> ActionExecutor:
    """Get global action executor."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor(event_bus)
    return _executor
