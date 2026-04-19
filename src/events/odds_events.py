"""
src/events/odds_events.py

Odds-related events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.events.base import BaseEvent, EventType


@dataclass
class OddsUpdated(BaseEvent):
    """Event emitted when odds are updated for a fixture."""
    event_type: EventType = field(default=EventType.ODDS_UPDATED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def market(self) -> str:
        """Market type: btts, ou25, h2h, etc."""
        return self.payload.get("market", "")

    @property
    def old_odds(self) -> dict[str, float] | None:
        return self.payload.get("old_odds")

    @property
    def new_odds(self) -> dict[str, float]:
        return self.payload.get("new_odds", {})

    @property
    def ev_change(self) -> float | None:
        """Change in expected value, or None if can't calculate."""
        return self.payload.get("ev_change")

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OddsUpdated:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class OddsStale(BaseEvent):
    """Event emitted when odds become stale (older than threshold)."""
    event_type: EventType = field(default=EventType.ODDS_STALE)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def age_hours(self) -> float:
        """How many hours since last update."""
        return self.payload.get("age_hours", 0)

    @property
    def threshold_hours(self) -> float:
        """The threshold that was exceeded."""
        return self.payload.get("threshold_hours", 24)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OddsStale:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


def emit_odds_updated(
    fixture_id: int,
    market: str,
    new_odds: dict[str, float],
    old_odds: dict[str, float] | None = None,
    ev_change: float | None = None,
) -> OddsUpdated:
    """Convenience function to emit OddsUpdated."""
    event = OddsUpdated(
        payload={
            "fixture_id": fixture_id,
            "market": market,
            "old_odds": old_odds,
            "new_odds": new_odds,
            "ev_change": ev_change,
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_odds_stale(fixture_id: int, market: str, age_hours: float, threshold_hours: float = 24.0) -> OddsStale:
    """Convenience function to emit OddsStale."""
    event = OddsStale(
        payload={
            "fixture_id": fixture_id,
            "market": market,
            "age_hours": age_hours,
            "threshold_hours": threshold_hours,
        }
    )
    from src.events.base import emit
    emit(event)
    return event
