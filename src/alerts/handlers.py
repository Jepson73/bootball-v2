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


# =========================================================
# Capital Allocator Handler
# =========================================================

class CapitalAllocatorHandler:
    """Handles bet allocation on BETS_GENERATED events."""
    
    def __init__(self, allocator=None):
        from src.betting.capital_allocator import get_capital_allocator
        self.allocator = allocator or get_capital_allocator()
        self._bankroll = 1000.0
        
        event_bus.subscribe(Events.BETS_GENERATED, self.on_bets_generated)
        logger.info("CapitalAllocatorHandler initialized")
    
    def update_bankroll(self, bankroll: float):
        self._bankroll = bankroll
        self.allocator.set_bankroll(bankroll)
    
    def on_bets_generated(self, event):
        from src.betting.capital_allocator import ValueBet
        from datetime import datetime
        
        data = event.data if hasattr(event, 'data') else event
        bets_data = data.get("bets", [])
        
        if not bets_data:
            return
        
        value_bets = []
        for bet_data in bets_data:
            try:
                kickoff_str = bet_data.get("kickoff")
                kickoff = (
                    datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                    if kickoff_str
                    else datetime.utcnow()
                )
                
                vb = ValueBet(
                    fixture_id=bet_data.get("fixture_id", 0),
                    market=bet_data.get("market", ""),
                    outcome=bet_data.get("outcome", ""),
                    odds=bet_data.get("odds", 0),
                    ev=bet_data.get("ev", 0),
                    our_prob=bet_data.get("our_prob", 0),
                    kelly_fraction=bet_data.get("kelly_fraction", 0),
                    league=bet_data.get("league", "Unknown"),
                    kickoff=kickoff
                )
                value_bets.append(vb)
            except Exception as e:
                logger.debug(f"Failed to parse bet: {e}")
        
        if not value_bets:
            return
        
        result = self.allocator.allocate(value_bets, self._bankroll)
        
        event_bus.emit(Events.BETS_ALLOCATED, {
            "allocated_bets": [
                {
                    "fixture_id": ab.fixture_id,
                    "market": ab.market,
                    "outcome": ab.outcome,
                    "stake": ab.stake,
                    "ev": ab.ev,
                    "kelly_fraction": ab.kelly_fraction,
                    "allocation_weight": ab.allocation_weight,
                    "risk_flags": ab.risk_flags
                }
                for ab in result.allocated_bets
            ],
            "total_stake": result.total_stake,
            "bankroll": result.bankroll,
            "exposure": result.exposure,
            "market_distribution": result.market_distribution,
            "rejected_count": result.rejected_count,
            "total_input_bets": result.total_input_bets
        })


_capital_allocator_handler = None


def get_capital_allocator_handler():
    global _capital_allocator_handler
    if _capital_allocator_handler is None:
        _capital_allocator_handler = CapitalAllocatorHandler()
    return _capital_allocator_handler
