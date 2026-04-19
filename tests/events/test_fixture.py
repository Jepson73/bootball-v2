"""
tests/events/test_fixture.py

Tests for fixture events.
"""
import pytest
from datetime import datetime, timezone

from src.events.fixture_events import (
    FixtureScheduled,
    FixtureUpdated,
    FixtureCompleted,
    emit_fixture_scheduled,
    emit_fixture_completed,
)
from src.events.base import EventType, get_emitter


class TestFixtureScheduled:
    def test_create(self):
        """Test FixtureScheduled creation."""
        date = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)
        event = FixtureScheduled(
            payload={
                "fixture_id": 123,
                "league_id": 39,
                "home_team_id": 50,
                "away_team_id": 51,
                "date": date,
            }
        )
        assert event.fixture_id == 123
        assert event.league_id == 39
        assert event.home_team_id == 50
        assert event.away_team_id == 51

    def test_to_dict(self):
        """Test serialization."""
        event = FixtureScheduled(
            payload={
                "fixture_id": 456,
                "league_id": 40,
            }
        )
        data = event.to_dict()
        assert data["event_type"] == "fixture.scheduled"
        assert data["payload"]["fixture_id"] == 456

    def test_from_dict(self):
        """Test deserialization."""
        data = {
            "event_type": "fixture.scheduled",
            "timestamp": "2026-04-19T12:00:00+00:00",
            "payload": {"fixture_id": 789, "league_id": 41},
        }
        event = FixtureScheduled.from_dict(data)
        assert event.fixture_id == 789
        assert event.event_type == EventType.FIXTURE_SCHEDULED

    def test_emit_convenience(self):
        """Test emit_fixture_scheduled convenience function."""
        emitter = get_emitter()
        emitter.clear()

        received = []
        emitter.subscribe(EventType.FIXTURE_SCHEDULED, lambda e: received.append(e))

        date = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)
        emit_fixture_scheduled(123, 39, 50, 51, date)

        assert len(received) == 1
        assert received[0].fixture_id == 123


class TestFixtureUpdated:
    def test_create(self):
        """Test FixtureUpdated creation."""
        event = FixtureUpdated(
            payload={
                "fixture_id": 123,
                "changes": {"status": "FT", "goals_home": 2},
            }
        )
        assert event.fixture_id == 123
        assert event.changes["status"] == "FT"

    def test_to_dict(self):
        """Test serialization."""
        event = FixtureUpdated(
            payload={"fixture_id": 456, "changes": {"home_goals": 1}}
        )
        data = event.to_dict()
        assert data["event_type"] == "fixture.updated"


class TestFixtureCompleted:
    def test_create(self):
        """Test FixtureCompleted creation."""
        event = FixtureCompleted(
            payload={
                "fixture_id": 123,
                "home_goals": 2,
                "away_goals": 1,
                "outcome": "H",
            }
        )
        assert event.fixture_id == 123
        assert event.home_goals == 2
        assert event.away_goals == 1
        assert event.outcome == "H"

    def test_outcome_values(self):
        """Test outcome values H/D/A."""
        h_event = FixtureCompleted(payload={"fixture_id": 1, "home_goals": 2, "away_goals": 1, "outcome": "H"})
        d_event = FixtureCompleted(payload={"fixture_id": 2, "home_goals": 1, "away_goals": 1, "outcome": "D"})
        a_event = FixtureCompleted(payload={"fixture_id": 3, "home_goals": 0, "away_goals": 2, "outcome": "A"})

        assert h_event.outcome == "H"
        assert d_event.outcome == "D"
        assert a_event.outcome == "A"

    def test_emit_convenience(self):
        """Test emit_fixture_completed convenience function."""
        emitter = get_emitter()
        emitter.clear()

        received = []
        emitter.subscribe(EventType.FIXTURE_COMPLETED, lambda e: received.append(e))

        emit_fixture_completed(123, 2, 1, "H")

        assert len(received) == 1
        assert received[0].home_goals == 2
        assert received[0].away_goals == 1
        assert received[0].outcome == "H"
