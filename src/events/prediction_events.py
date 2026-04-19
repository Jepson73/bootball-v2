"""
src/events/prediction_events.py

Prediction-related events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.events.base import BaseEvent, EventType


@dataclass
class PredictionCreated(BaseEvent):
    """Event emitted when a new prediction is generated."""
    event_type: EventType = field(default=EventType.PREDICTION_CREATED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def predicted_outcome(self) -> str:
        return self.payload.get("predicted_outcome", "")

    @property
    def our_prob(self) -> float:
        return self.payload.get("our_prob", 0.0)

    @property
    def odds(self) -> float:
        return self.payload.get("odds", 0.0)

    @property
    def ev(self) -> float:
        return self.payload.get("ev", 0.0)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PredictionCreated:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=__import__("datetime").datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class PredictionSettled(BaseEvent):
    """Event emitted when a prediction is settled."""
    event_type: EventType = field(default=EventType.PREDICTION_SETTLED)

    @property
    def fixture_id(self) -> int:
        return self.payload.get("fixture_id", 0)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def predicted_outcome(self) -> str:
        return self.payload.get("predicted_outcome", "")

    @property
    def actual_outcome(self) -> str:
        return self.payload.get("actual_outcome", "")

    @property
    def won(self) -> bool:
        return self.payload.get("won", False)

    @property
    def pnl(self) -> float | None:
        return self.payload.get("pnl")

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PredictionSettled:
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=__import__("datetime").datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


def emit_prediction_created(
    fixture_id: int,
    market: str,
    predicted_outcome: str,
    our_prob: float,
    odds: float,
    ev: float,
) -> PredictionCreated:
    """Convenience function to emit PredictionCreated."""
    event = PredictionCreated(
        payload={
            "fixture_id": fixture_id,
            "market": market,
            "predicted_outcome": predicted_outcome,
            "our_prob": our_prob,
            "odds": odds,
            "ev": ev,
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_prediction_settled(
    fixture_id: int,
    market: str,
    predicted_outcome: str,
    actual_outcome: str,
    won: bool,
    pnl: float | None = None,
) -> PredictionSettled:
    """Convenience function to emit PredictionSettled."""
    event = PredictionSettled(
        payload={
            "fixture_id": fixture_id,
            "market": market,
            "predicted_outcome": predicted_outcome,
            "actual_outcome": actual_outcome,
            "won": won,
            "pnl": pnl,
        }
    )
    from src.events.base import emit
    emit(event)
    return event
