"""
src/models/model_tracker.py

Model tracking through iterations.
Tracks each model version with metrics, records retraining events.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ModelIteration:
    """A single model iteration/version."""
    version_number: int
    version_name: str | None
    brier_score: float
    accuracy: float
    ece: float
    sample_size: int
    is_active: bool
    trained_at: datetime
    metrics_history: list[dict]


@dataclass
class LifecycleGraph:
    """Graph data for model lifecycle visualization."""
    market: str
    iterations: list[ModelIteration]
    retrain_events: list[dict]
    current_brier: float
    baseline_brier: float
    drift_score: float
    overall_trend: str


class ModelTracker:
    """Tracks model versions and their metrics over time.

    Provides iteration history and lifecycle graph data.
    """

    def __init__(self, market: str):
        self.market = market

    def get_iterations(self, limit: int = 50) -> list[ModelIteration]:
        """Get model iterations from database.

        Returns list of ModelIteration objects ordered by version_number.
        """
        try:
            from src.storage.db import get_session
            from src.storage.models import ModelVersion
            from sqlalchemy import select

            with get_session() as s:
                versions = s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.market == self.market)
                    .order_by(ModelVersion.version_number.desc())
                    .limit(limit)
                ).scalars().all()

                return [self._version_to_iteration(v) for v in versions]
        except Exception as e:
            logger.warning(f"Failed to load iterations: {e}")
            return []

    def _version_to_iteration(self, version) -> ModelIteration:
        """Convert ModelVersion DB model to ModelIteration dataclass."""
        return ModelIteration(
            version_number=version.version_number,
            version_name=version.version_name,
            brier_score=version.brier_score,
            accuracy=version.accuracy,
            ece=version.ece,
            sample_size=version.sample_size,
            is_active=version.is_active,
            trained_at=version.trained_at,
            metrics_history=[],
        )

    def record_training(
        self,
        version_number: int,
        brier_score: float,
        accuracy: float,
        ece: float,
        sample_size: int,
        version_name: str | None = None,
        model_type: str = "ensemble",
        features_used: str | None = None,
    ) -> bool:
        """Record a new model training event.

        Returns True if successful, False otherwise.
        """
        try:
            from src.storage.db import get_session
            from src.storage.models import ModelVersion

            with get_session() as s:
                existing = s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.market == self.market)
                    .where(ModelVersion.version_number == version_number)
                ).scalar_one_or_none()

                if existing:
                    existing.brier_score = brier_score
                    existing.accuracy = accuracy
                    existing.ece = ece
                    existing.sample_size = sample_size
                    existing.version_name = version_name
                    existing.is_active = True
                else:
                    s.execute(
                        select(ModelVersion)
                        .where(ModelVersion.market == self.market)
                        .where(ModelVersion.is_active == True)
                    ).scalars().all()
                    for v in s:
                        v.is_active = False

                    new_version = ModelVersion(
                        market=self.market,
                        version_number=version_number,
                        version_name=version_name,
                        brier_score=brier_score,
                        accuracy=accuracy,
                        ece=ece,
                        sample_size=sample_size,
                        model_type=model_type,
                        features_used=features_used,
                        is_active=True,
                    )
                    s.add(new_version)

                s.commit()
                logger.info(f"Recorded training for {self.market} v{version_number}")
                return True
        except Exception as e:
            logger.error(f"Failed to record training: {e}")
            return False

    def record_retrain(
        self,
        old_version_id: int | None,
        new_version_id: int,
        reason: str,
        reason_detail: str | None = None,
        brier_score_before: float | None = None,
        brier_score_after: float | None = None,
        triggered_by_drift: bool = False,
        drift_score: float | None = None,
    ) -> bool:
        """Record a retraining event.

        Returns True if successful, False otherwise.
        """
        try:
            from src.storage.db import get_session
            from src.storage.models import RetrainEvent

            with get_session() as s:
                event = RetrainEvent(
                    market=self.market,
                    old_version_id=old_version_id,
                    new_version_id=new_version_id,
                    reason=reason,
                    reason_detail=reason_detail,
                    brier_score_before=brier_score_before,
                    brier_score_after=brier_score_after,
                    triggered_by_drift=triggered_by_drift,
                    drift_score=drift_score,
                )
                s.add(event)
                s.commit()
                logger.info(f"Recorded retrain for {self.market}: {reason}")
                
                from src.events.event_bus import event_bus, Events
                event_bus.emit(Events.MODEL_TREND, {
                    "market": self.market,
                    "old_version_id": old_version_id,
                    "new_version_id": new_version_id,
                    "reason": reason,
                    "brier_score_before": brier_score_before,
                    "brier_score_after": brier_score_after,
                    "drift_score": drift_score,
                    "summary": f"Model retrain: {self.market}, {reason}"
                })
                
                return True
        except Exception as e:
            logger.error(f"Failed to record retrain: {e}")
            return False

    def get_lifecycle_graph(self, lookback_days: int = 90) -> LifecycleGraph:
        """Get lifecycle graph data for visualization.

        Returns LifecycleGraph with iterations and retrain events.
        """
        iterations = self.get_iterations(limit=100)

        retrain_events = self._get_retrain_events()

        if iterations:
            current_brier = iterations[0].brier_score
            baseline_brier = self._compute_baseline(iterations)
            drift_score = current_brier - baseline_brier if baseline_brier else 0
            overall_trend = self._compute_trend(iterations)
        else:
            current_brier = 0
            baseline_brier = 0
            drift_score = 0
            overall_trend = "unknown"

        return LifecycleGraph(
            market=self.market,
            iterations=iterations,
            retrain_events=retrain_events,
            current_brier=current_brier,
            baseline_brier=baseline_brier,
            drift_score=drift_score,
            overall_trend=overall_trend,
        )

    def _get_retrain_events(self) -> list[dict]:
        """Get retrain events for this market."""
        try:
            from src.storage.db import get_session
            from src.storage.models import RetrainEvent
            from sqlalchemy import select

            with get_session() as s:
                events = s.execute(
                    select(RetrainEvent)
                    .where(RetrainEvent.market == self.market)
                    .order_by(RetrainEvent.created_at.desc())
                    .limit(20)
                ).scalars().all()

                return [
                    {
                        "id": e.id,
                        "reason": e.reason,
                        "reason_detail": e.reason_detail,
                        "brier_score_before": e.brier_score_before,
                        "brier_score_after": e.brier_score_after,
                        "triggered_by_drift": e.triggered_by_drift,
                        "drift_score": e.drift_score,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in events
                ]
        except Exception as e:
            logger.warning(f"Failed to load retrain events: {e}")
            return []

    def _compute_baseline(self, iterations: list[ModelIteration]) -> float:
        """Compute baseline Brier score (average of older iterations)."""
        if len(iterations) < 3:
            return 0
        older = iterations[-3:]
        return sum(i.brier_score for i in older) / len(older)

    def _compute_trend(self, iterations: list[ModelIteration]) -> str:
        """Compute overall trend direction."""
        if len(iterations) < 5:
            return "insufficient_data"

        recent = iterations[:3]
        older = iterations[-3:]

        recent_avg = sum(i.brier_score for i in recent) / 3
        older_avg = sum(i.brier_score for i in older) / 3

        delta = recent_avg - older_avg

        if abs(delta) < 0.01:
            return "stable"
        elif delta < 0:
            return "improving"
        else:
            return "degrading"


class ModelTrackerCache:
    """In-memory cache for model trackers per market."""

    def __init__(self):
        self._trackers: dict[str, ModelTracker] = {}

    def get_tracker(self, market: str) -> ModelTracker:
        """Get or create tracker for market."""
        if market not in self._trackers:
            self._trackers[market] = ModelTracker(market)
        return self._trackers[market]


_tracker_cache: ModelTrackerCache | None = None


def get_model_tracker(market: str) -> ModelTracker:
    """Convenience function to get model tracker."""
    global _tracker_cache
    if _tracker_cache is None:
        _tracker_cache = ModelTrackerCache()
    return _tracker_cache.get_tracker(market)
