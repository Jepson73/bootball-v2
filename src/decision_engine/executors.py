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
        """Kick off model retraining via ModelRegistry in a background thread."""
        market = payload.get("market")
        reason = payload.get("reason", "decision_engine")

        logger.info("Executing RETRAIN_MODEL for market: %s (%s)", market, reason)

        self.event_bus.emit(Events.MODEL_RETRAIN_STARTED, {
            "market": market,
            "reason": reason,
            "summary": f"Retrain started: {market}",
        })

        import threading
        threading.Thread(
            target=self._run_retrain_background,
            args=(market, reason),
            daemon=True,
        ).start()

    def _run_retrain_background(self, market: str, reason: str) -> None:
        """Run full retrain (model + calibrator) via ModelRegistry and notify Discord."""
        import json, os, urllib.request
        from datetime import datetime as _dt

        def _discord(embed: dict) -> None:
            url = os.getenv("DISCORD_WEBHOOK_URL")
            if not url:
                return
            try:
                data = json.dumps({"embeds": [embed]}).encode()
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as exc:
                logger.error("Discord notify failed: %s", exc)

        try:
            from scripts.web_ui import _train_market_with_calibration
            result = _train_market_with_calibration(market, reason=f"auto_{reason}")

            label = result.get("version_label", "unknown")
            brier = result.get("brier_score", 0) or 0

            self.event_bus.emit(Events.MODEL_RETRAIN_COMPLETED, {
                "market": market,
                "version_label": label,
                "brier_score": brier,
                "summary": f"Retrain complete {market} → {label}",
            })

            _discord({
                "title": f"✅ RETRAIN COMPLETE: {market.upper()}",
                "description": "Automatic retraining triggered by model degradation",
                "color": 3066993,
                "fields": [
                    {"name": "Market", "value": market.upper(), "inline": True},
                    {"name": "New Version", "value": f"`{label}`", "inline": True},
                    {"name": "Brier Score", "value": f"{brier:.4f}", "inline": True},
                    {"name": "Reason", "value": reason, "inline": False},
                ],
                "timestamp": _dt.utcnow().isoformat(),
            })
            logger.info("Auto-retrain complete: %s → %s (brier=%.4f)", market, label, brier)

        except Exception as e:
            logger.exception("Auto-retrain failed for %s", market)
            _discord({
                "title": f"❌ RETRAIN FAILED: {market.upper()}",
                "description": str(e),
                "color": 15158332,
                "timestamp": _dt.utcnow().isoformat(),
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
