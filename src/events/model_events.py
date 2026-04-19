"""
src/events/model_events.py

Model-related events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.events.base import BaseEvent, EventType


@dataclass
class ModelTrained(BaseEvent):
    """Event emitted when a new model is trained."""
    event_type: EventType = field(default=EventType.MODEL_TRAINED)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def model_name(self) -> str:
        return self.payload.get("model_name", "")

    @property
    def version(self) -> int:
        return self.payload.get("version", 0)

    @property
    def brier_score(self) -> float | None:
        return self.payload.get("brier_score")

    @property
    def samples_trained(self) -> int:
        return self.payload.get("samples_trained", 0)

    @property
    def features_used(self) -> list[str]:
        return self.payload.get("features_used", [])

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelTrained:
        from datetime import datetime
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class ModelDegraded(BaseEvent):
    """Event emitted when model quality degrades."""
    event_type: EventType = field(default=EventType.MODEL_DEGRADED)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def model_name(self) -> str:
        return self.payload.get("model_name", "")

    @property
    def current_brier(self) -> float:
        return self.payload.get("current_brier", 0.0)

    @property
    def threshold(self) -> float:
        return self.payload.get("threshold", 0.25)

    @property
    def degradation_percent(self) -> float:
        return self.payload.get("degradation_percent", 0.0)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelDegraded:
        from datetime import datetime
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


@dataclass
class ModelActivated(BaseEvent):
    """Event emitted when a model version is made active."""
    event_type: EventType = field(default=EventType.MODEL_ACTIVATED)

    @property
    def market(self) -> str:
        return self.payload.get("market", "")

    @property
    def model_name(self) -> str:
        return self.payload.get("model_name", "")

    @property
    def version(self) -> int:
        return self.payload.get("version", 0)

    @property
    def previous_version(self) -> int | None:
        return self.payload.get("previous_version")

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelActivated:
        from datetime import datetime
        return cls(
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
        )


def emit_model_trained(
    market: str,
    model_name: str,
    version: int,
    brier_score: float | None = None,
    samples_trained: int = 0,
    features_used: list[str] | None = None,
) -> ModelTrained:
    """Convenience function to emit ModelTrained."""
    event = ModelTrained(
        payload={
            "market": market,
            "model_name": model_name,
            "version": version,
            "brier_score": brier_score,
            "samples_trained": samples_trained,
            "features_used": features_used or [],
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_model_degraded(
    market: str,
    model_name: str,
    current_brier: float,
    threshold: float = 0.25,
) -> ModelDegraded:
    """Convenience function to emit ModelDegraded."""
    degradation = ((current_brier - threshold) / threshold) * 100 if threshold > 0 else 0
    event = ModelDegraded(
        payload={
            "market": market,
            "model_name": model_name,
            "current_brier": current_brier,
            "threshold": threshold,
            "degradation_percent": degradation,
        }
    )
    from src.events.base import emit
    emit(event)
    return event


def emit_model_activated(
    market: str,
    model_name: str,
    version: int,
    previous_version: int | None = None,
) -> ModelActivated:
    """Convenience function to emit ModelActivated."""
    event = ModelActivated(
        payload={
            "market": market,
            "model_name": model_name,
            "version": version,
            "previous_version": previous_version,
        }
    )
    from src.events.base import emit
    emit(event)
    return event
