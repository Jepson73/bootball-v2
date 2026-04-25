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
    PREDICTIONS_GENERATED = "predictions_generated"
    
    # Health system
    HEALTH_UPDATE = "health_update"
    
    # Model system
    MODEL_TREND = "model_trend"
