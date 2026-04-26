"""
Alert Handlers - Process and route alerts with suppression logic.

Handles:
- Suppression of useless alerts (0 bets, etc.)
- Alert routing to Discord
- Cooldown management
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from src.alerts.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


# Cooldown tracking
_last_alert_time: dict[str, datetime] = {}
ALERT_COOLDOWN_SECONDS = 300  # 5 minutes


def should_suppress(alert_type: str) -> bool:
    """Check if alert should be suppressed due to cooldown."""
    now = datetime.utcnow()
    last_time = _last_alert_time.get(alert_type)
    
    if last_time and (now - last_time).total_seconds() < ALERT_COOLDOWN_SECONDS:
        return True
    
    _last_alert_time[alert_type] = now
    return False


def is_useful_run_finished(data: dict) -> bool:
    """Check if RUN_FINISHED event contains useful information."""
    total_bets = data.get("total_bets", 0)
    total_ev = data.get("total_ev", 0)
    errors = data.get("errors", [])
    
    # Suppress if: 0 bets AND 0 EV AND no errors
    if total_bets == 0 and total_ev == 0 and not errors:
        logger.info("[ALERT] Suppressing: RUN_FINISHED with 0 bets, 0 EV, no errors")
        return False
    
    return True


def is_useful_bets_generated(data: dict) -> bool:
    """Check if BETS_GENERATED event contains useful information."""
    bets = data.get("bets", [])
    
    # If we have bets, always useful
    if bets:
        return True
    
    logger.info("[ALERT] Suppressing: BETS_GENERATED with 0 bets")
    return False


def is_useful_predictions_generated(data: dict) -> bool:
    """Check if PREDICTIONS_GENERATED is useful."""
    prediction_count = data.get("prediction_count", 0)
    fixture_count = data.get("fixture_count", 0)
    
    # If no predictions, suppress
    if prediction_count == 0:
        logger.info("[ALERT] Suppressing: PREDICTIONS_GENERATED with 0 predictions")
        return False
    
    return True


def handle_alert_triggered(event) -> dict:
    """Handle ALERT_TRIGGERED events."""
    data = event.data
    title = data.get("title", "Alert")
    severity = data.get("severity", "info")
    
    logger.info(f"[ALERT] {title}: {data.get('message', '')}")
    
    return {
        "handled": True,
        "title": title,
        "severity": severity
    }


def handle_run_finished(event) -> dict:
    """Handle RUN_FINISHED events with suppression."""
    data = event.data
    
    if not is_useful_run_finished(data):
        return {"handled": False, "reason": "suppressed_useless"}
    
    return {"handled": True, "data": data}


def handle_bets_generated(event) -> dict:
    """Handle BETS_GENERATED events with suppression."""
    data = event.data
    
    if not is_useful_bets_generated(data):
        return {"handled": False, "reason": "suppressed_zero_bets"}
    
    return {"handled": True, "data": data}


def handle_predictions_generated(event) -> dict:
    """Handle PREDICTIONS_GENERATED events with suppression."""
    data = event.data
    
    if not is_useful_predictions_generated(data):
        return {"handled": False, "reason": "suppressed_zero_predictions"}
    
    return {"handled": True, "data": data}


def setup_alert_handlers() -> None:
    """Setup alert handlers for EventBus."""
    
    def on_run_finished(event: Event):
        result = handle_run_finished(event)
        if result.get("handled"):
            # Forward to Discord handler
            event_bus.emit(Events.NOTIFICATION_DISCORD, {
                "title": "Daily Run Complete",
                "description": f"Bets: {result['data'].get('total_bets', 0)}, "
                              f"EV: {result['data'].get('total_ev', 0):.2%}, "
                              f"Duration: {result['data'].get('duration', 0):.1f}s",
                "severity": "info"
            })
    
    def on_bets_generated(event: Event):
        result = handle_bets_generated(event)
        if result.get("handled"):
            bets = result["data"].get("bets", [])
            total_ev = sum(b.get("ev", 0) for b in bets)
            event_bus.emit(Events.NOTIFICATION_DISCORD, {
                "title": "Value Bets Found",
                "description": f"{len(bets)} bets with total EV: {total_ev:.2%}",
                "severity": "success"
            })
    
    def on_predictions_generated(event: Event):
        result = handle_predictions_generated(event)
        if result.get("handled"):
            # Don't send Discord for predictions - too verbose
            pass
    
    def on_alert_triggered(event: Event):
        result = handle_alert_triggered(event)
        if result.get("handled"):
            data = event.data
            event_bus.emit(Events.NOTIFICATION_DISCORD, {
                "title": data.get("title", "Alert"),
                "description": data.get("message", ""),
                "severity": data.get("severity", "info")
            })
    
    # Subscribe to relevant events
    event_bus.subscribe(Events.RUN_FINISHED, on_run_finished)
    event_bus.subscribe(Events.BETS_GENERATED, on_bets_generated)
    event_bus.subscribe(Events.PREDICTIONS_GENERATED, on_predictions_generated)
    event_bus.subscribe(Events.ALERT_TRIGGERED, on_alert_triggered)
    
    logger.info("Alert handlers registered")
