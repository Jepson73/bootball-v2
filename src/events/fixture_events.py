"""
src/events/fixture_events.py

Fixture-related events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.events.base import BaseEvent, EventType


@dataclass
class FixtureScheduled(BaseEvent):
    """Event emitted when a new fixture is scheduled."""
    event_type: EventType = field(default=EventType.FIXTURE_SCHEDULED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def league_id(self) -> int:
        return self.payload.get("league_id", 0)

    @property
    def home_team_id(self) -> int:
        return self.payload.get("home_team_id", 0)

    @property
    def away_team_id(self) -> int:
        return self.payload.get("away_team_id", 0)

    @property
    def date(self) -> datetime:
        return self.payload.get("date", datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FixtureScheduled:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class FixtureUpdated(BaseEvent):
    """Event emitted when fixture details change."""
    event_type: EventType = field(default=EventType.FIXTURE_UPDATED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def changes(self) -> dict[str, Any]:
        return self.payload.get("changes", {})

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FixtureUpdated:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class FixtureCompleted(BaseEvent):
    """Event emitted when a fixture is completed (FT)."""
    event_type: EventType = field(default=EventType.FIXTURE_COMPLETED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def home_goals(self) -> int | None:
        return self.payload.get("home_goals")

    @property
    def away_goals(self) -> int | None:
        return self.payload.get("away_goals")

    @property
    def outcome(self) -> str | None:
        """H (home win), D (draw), A (away win)"""
        return self.payload.get("outcome")

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FixtureCompleted:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


def emit_fixture_scheduled(fixture_id: int, league_id: int, home_id: int, away_id: int, date: datetime) -> FixtureScheduled:
    """Convenience function to emit FixtureScheduled."""
    event = FixtureScheduled(
        payload={
            "fixture_id": fixture_id,
            "league_id": league_id,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "date": date.isoformat() if date else None,
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_fixture_updated(fixture_id: int, changes: dict[str, Any]) -> FixtureUpdated:
    """Convenience function to emit FixtureUpdated."""
    event = FixtureUpdated(
        payload={
            "fixture_id": fixture_id,
            "changes": changes,
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_fixture_completed(fixture_id: int, home_goals: int, away_goals: int, outcome: str) -> FixtureCompleted:
    """Convenience function to emit FixtureCompleted."""
    event = FixtureCompleted(
        payload={
            "fixture_id": fixture_id,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "outcome": outcome,
        }
    )
    from src.events.base import emit
    emit(event)
    return event
