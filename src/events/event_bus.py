"""
Simple in-process event bus for alerts.

Purpose:
- Decouple betting / runs / model logic from Discord
- Prevent direct coupling to webhook logic
"""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable
import logging

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)
        self._event_log: list[dict] = []
        self._max_log_size = 1000

    def subscribe(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers[event_type].append(handler)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            **payload
        }
        
        # Log event
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]
        
        summary = payload.get('summary', str(payload)[:80])
        logger.info(f"[EventBus] {event_type}: {summary}")
        
        # Call registered handlers
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"[EventBus] handler failed for {event_type}: {e}")
        
        # Dispatch to consumer registry (event-driven architecture)
        self._dispatch_to_registry(event_type, payload)
        
        # Persist to event store for replay
        self._persist_event(event)

    def _dispatch_to_registry(self, event_type: str, payload: dict[str, Any]) -> None:
        """Dispatch event to registered consumers."""
        try:
            from src.events.consumers.registry import registry
            registry.dispatch_event(event_type, payload)
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"[EventBus] registry dispatch failed for {event_type}: {e}")
    
    def _persist_event(self, event: dict[str, Any]) -> None:
        """Persist event to event store for replay."""
        try:
            from src.events.event_store import get_event_store
            store = get_event_store()
            store.append_event(event)
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"[EventBus] event persistence failed: {e}")

    def get_log(self, event_type: str | None = None, limit: int = 100) -> list[dict]:
        """Get recent event log, optionally filtered by event_type."""
        if event_type:
            return [e for e in self._event_log if e.get('event_type') == event_type][-limit:]
        return self._event_log[-limit:]


# global singleton
event_bus = EventBus()


# Canonical event type constants
class Events:
    # Betting system
    BETS_GENERATED = "bets_generated"
    BET_SETTLED = "bet_settled"
    BETS_SETTLED = "bets_settled"
    
    # Run system
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_COMPLETED = "run_completed"
    PREDICTIONS_GENERATED = "predictions_generated"
    
    # Health system
    HEALTH_UPDATE = "health_update"
    
    # Model system
    MODEL_TREND = "model_trend"
    
    # Notifications
    NOTIFICATION_DISCORD = "notification_discord"
    STATE_CHANGED = "state_changed"
    ALERT_TRIGGERED = "alert_triggered"
    BETS_ALLOCATED = "bets_allocated"
    
    # Execution
    BET_PLACED = "bet_placed"
    BET_REJECTED = "bet_rejected"
    EXECUTION_SUMMARY = "execution_summary"
    BET_SETTLED = "bet_settled"
    BETS_SETTLED = "bets_settled"
    PORTFOLIO_BUILT = "portfolio_built"
    
    # Performance & Allocation
    PERFORMANCE_UPDATE = "performance_update"
    ALLOCATION_UPDATED = "allocation_updated"
    CORRELATION_ANALYZED = "correlation_analyzed"
    PORTFOLIO_OPTIMIZED = "portfolio_optimized"
    
    # Portfolio State (stateful system)
    PORTFOLIO_STATE_LOADED = "portfolio_state_loaded"
    PORTFOLIO_STATE_UPDATED = "portfolio_state_updated"
    
    # Policy Engine (governance)
    POLICY_APPROVED = "policy_approved"
    POLICY_THROTTLED = "policy_throttled"
    POLICY_REJECTED = "policy_rejected"
    RISK_LIMIT_BREACHED = "risk_limit_breached"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    
    # Execution Spine Guard (governance)
    EXECUTION_SOURCED_FROM_ILLEGAL_PATH = "execution_sourced_from_illegal_path"
    EXECUTION_VALIDATED = "execution_validated"
    EXECUTION_REJECTED = "execution_rejected"
    
    # Calibration (state convergence)
    CALIBRATION_DRIFT_DETECTED = "calibration_drift_detected"
    MODEL_BIAS_ADJUSTED = "model_bias_adjusted"
    RISK_MODEL_CORRECTED = "risk_model_corrected"
    PORTFOLIO_REWEIGHTING_SUGGESTED = "portfolio_reweighting_suggested"
    CALIBRATION_REPORT_READY = "calibration_report_ready"
    
    # Meta-Policy Learning (policy self-tuning)
    META_POLICY_ADJUSTED = "meta_policy_adjusted"
    POLICY_OVERFITTING_DETECTED = "policy_overfitting_detected"
    RISK_APPETITE_INCREASED = "risk_appetite_increased"
    RISK_APPETITE_REDUCED = "risk_appetite_reduced"
    CONSTRAINT_STABILIZED = "constraint_stabilized"
    
    # Simulation (Monte Carlo)
    MONTE_CARLO_COMPLETED = "monte_carlo_completed"
    
    # Feedback Loop (closed-loop)
    PERFORMANCE_COMPUTED = "performance_computed"
    CALIBRATION_UPDATED = "calibration_updated"
    POLICY_ADAPTED = "policy_adapted"
    RUN_FEEDBACK_COMPLETED = "run_feedback_completed"
    
    # Closed Loop Validation (self-adaptation verification)
    CLOSED_LOOP_VALIDATION_COMPLETED = "closed_loop_validation_completed"
    SYSTEM_ADAPTIVE_CONFIRMED = "system_adaptive_confirmed"
    SYSTEM_STATIC_DETECTED = "system_static_detected"
    ADAPTATION_SCORE_UPDATED = "adaptation_score_updated"

    # Model lifecycle (auto training / recalibration)
    MODEL_RETRAIN_STARTED = "model_retrain_started"
    MODEL_RETRAIN_COMPLETED = "model_retrain_completed"
    MODEL_RECALIBRATION_COMPLETED = "model_recalibration_completed"

    # Settlement integrity (Phase 30) — verify-guard corrections, forward-dated-live
    # catches, DEAD-mark spikes. Data-integrity signal, distinct from betting/policy.
    SETTLEMENT_INTEGRITY_EVENT = "settlement_integrity_event"
