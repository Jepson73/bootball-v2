"""
src/events/base.py

Event-driven architecture base classes.
All events inherit from BaseEvent and are immutable once created.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventType(Enum):
    """Registry of all event types in the system."""
    # Fixture events
    FIXTURE_SCHEDULED = "fixture.scheduled"
    FIXTURE_UPDATED = "fixture.updated"
    FIXTURE_COMPLETED = "fixture.completed"

    # Odds events
    ODDS_UPDATED = "odds.updated"
    ODDS_STALE = "odds.stale"

    # Prediction events
    PREDICTION_CREATED = "prediction.created"
    PREDICTION_SETTLED = "prediction.settled"

    # Bet events
    BET_PLACED = "bet.placed"
    BET_SETTLED = "bet.settled"

    # Model events
    MODEL_TRAINED = "model.trained"
    MODEL_DEGRADED = "model.degraded"
    MODEL_ACTIVATED = "model.activated"


@dataclass
class BaseEvent(ABC):
    """Base class for all events.

    Events are:
    - Immutable once created
    - Serializable to JSON
    - Have a timestamp
    - Have a type from EventType enum
    """
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate event after creation."""
        if not isinstance(self.event_type, EventType):
            raise TypeError(f"event_type must be EventType, got {type(self.event_type)}")

    @abstractmethod
    def to_dict(self) -> dict:
        """Convert event to dictionary for serialization."""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict) -> BaseEvent:
        """Create event from dictionary."""
        pass

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> BaseEvent:
        """Deserialize event from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @property
    def type_name(self) -> str:
        return self.event_type.value


class EventEmitter:
    """Simple in-process event emitter.

    In production, this would be replaced with a message broker
    (Redis, RabbitMQ, Kafka) for multi-user real-time support.
    """

    def __init__(self):
        self._handlers: dict[EventType, list[callable]] = {}

    def subscribe(self, event_type: EventType, handler: callable) -> None:
        """Subscribe a handler to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: callable) -> None:
        """Unsubscribe a handler."""
        if event_type in self._handlers:
            self._handlers[event_type].remove(handler)

    def emit(self, event: BaseEvent) -> None:
        """Emit an event to all subscribers."""
        if event.event_type in self._handlers:
            for handler in self._handlers[event.event_type]:
                try:
                    handler(event)
                except Exception as e:
                    # Log but don't block - handlers shouldn't break emission
                    import logging
                    logging.getLogger(__name__).error(f"Handler error: {e}")

    def clear(self) -> None:
        """Clear all handlers."""
        self._handlers.clear()


# Global emitter instance
_emitter = EventEmitter()


def get_emitter() -> EventEmitter:
    """Get the global event emitter."""
    return _emitter


def emit(event: BaseEvent) -> None:
    """Emit an event using the global emitter."""
    _emitter.emit(event)
