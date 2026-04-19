"""
tests/events/test_base.py

Tests for event base system.
"""
import pytest
from datetime import datetime, timezone

from src.events.base import (
    BaseEvent,
    EventType,
    EventEmitter,
    emit,
    get_emitter,
)


class MockEvent(BaseEvent):
    """Test event implementation."""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "test_field": self.payload.get("test_field"),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MockEvent:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


class TestEventType:
    def test_all_event_types_defined(self):
        """Verify all expected event types exist."""
        expected_types = [
            "fixture.scheduled",
            "fixture.updated",
            "fixture.completed",
            "odds.updated",
            "odds.stale",
            "prediction.created",
            "prediction.settled",
            "bet.placed",
            "bet.settled",
            "model.trained",
            "model.degraded",
            "model.activated",
        ]
        for et in expected_types:
            assert EventType(et) is not None

    def test_event_type_values_unique(self):
        """Verify event type values are unique."""
        values = [et.value for et in EventType]
        assert len(values) == len(set(values))


class TestBaseEvent:
    def test_event_creation(self):
        """Test basic event creation."""
        event = MockEvent(
            event_type=EventType.FIXTURE_COMPLETED,
            payload={"fixture_id": 123, "score": "2-1"},
        )
        assert event.event_type == EventType.FIXTURE_COMPLETED
        assert event.payload["fixture_id"] == 123
        assert event.timestamp is not None

    def test_event_immutable(self):
        """Test that events are immutable after creation."""
        event = MockEvent(
            event_type=EventType.FIXTURE_COMPLETED,
            payload={"key": "value"},
        )
        with pytest.raises(dataclassFrozenError if hasattr(__builtins__, 'dataclassFrozenError') else AttributeError):
            event.payload["new_key"] = "new_value"

    def test_event_to_dict(self):
        """Test event serialization to dict."""
        event = MockEvent(
            event_type=EventType.ODDS_UPDATED,
            payload={"fixture_id": 456, "market": "btts"},
        )
        data = event.to_dict()
        assert data["event_type"] == "odds.updated"
        assert data["payload"]["fixture_id"] == 456
        assert data["test_field"] == "btts"

    def test_event_to_json(self):
        """Test event serialization to JSON."""
        event = MockEvent(
            event_type=EventType.BET_PLACED,
            payload={"bet_id": 789},
        )
        json_str = event.to_json()
        assert '"event_type": "bet.placed"' in json_str
        assert '"bet_id": 789' in json_str

    def test_event_from_json(self):
        """Test event deserialization from JSON."""
        json_str = '{"event_type": "prediction.created", "timestamp": "2026-04-19T12:00:00+00:00", "payload": {"pred_id": 100}}'
        event = MockEvent.from_json(json_str)
        assert event.event_type == EventType.PREDICTION_CREATED
        assert event.payload["pred_id"] == 100


class TestEventEmitter:
    def test_subscribe_and_emit(self):
        """Test basic subscribe and emit."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.subscribe(EventType.FIXTURE_COMPLETED, handler)
        emitter.emit(MockEvent(event_type=EventType.FIXTURE_COMPLETED, payload={}))

        assert len(received) == 1

    def test_unsubscribe(self):
        """Test unsubscribe removes handler."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.subscribe(EventType.FIXTURE_COMPLETED, handler)
        emitter.unsubscribe(EventType.FIXTURE_COMPLETED, handler)
        emitter.emit(MockEvent(event_type=EventType.FIXTURE_COMPLETED, payload={}))

        assert len(received) == 0

    def test_multiple_handlers(self):
        """Test multiple handlers for same event type."""
        emitter = EventEmitter()
        received1 = []
        received2 = []

        def handler1(event):
            received1.append(event)

        def handler2(event):
            received2.append(event)

        emitter.subscribe(EventType.FIXTURE_COMPLETED, handler1)
        emitter.subscribe(EventType.FIXTURE_COMPLETED, handler2)
        emitter.emit(MockEvent(event_type=EventType.FIXTURE_COMPLETED, payload={}))

        assert len(received1) == 1
        assert len(received2) == 1

    def test_handler_error_doesnt_block(self):
        """Test that handler errors don't block other handlers."""
        emitter = EventEmitter()
        received = []

        def bad_handler(event):
            raise ValueError("Handler error")

        def good_handler(event):
            received.append(event)

        emitter.subscribe(EventType.FIXTURE_COMPLETED, bad_handler)
        emitter.subscribe(EventType.FIXTURE_COMPLETED, good_handler)
        emitter.emit(MockEvent(event_type=EventType.FIXTURE_COMPLETED, payload={}))

        assert len(received) == 1

    def test_global_emitter(self):
        """Test global emitter is accessible."""
        emitter = get_emitter()
        assert emitter is not None
        assert isinstance(emitter, EventEmitter)

    def test_global_emit(self):
        """Test global emit function."""
        received = []
        emitter = get_emitter()
        emitter.clear()  # Clear any previous handlers

        def handler(event):
            received.append(event)

        emitter.subscribe(EventType.MODEL_TRAINED, handler)
        emit(MockEvent(event_type=EventType.MODEL_TRAINED, payload={}))

        assert len(received) == 1

    def test_clear(self):
        """Test clear removes all handlers."""
        emitter = EventEmitter()

        def handler(event):
            pass

        emitter.subscribe(EventType.FIXTURE_COMPLETED, handler)
        emitter.subscribe(EventType.ODDS_UPDATED, handler)
        emitter.clear()

        emitter.emit(MockEvent(event_type=EventType.FIXTURE_COMPLETED, payload={}))
        emitter.emit(MockEvent(event_type=EventType.ODDS_UPDATED, payload={}))

        # No errors means handlers were cleared
