"""
tests/models/test_model_tracker.py

Tests for model tracking and lifecycle management.
"""
import sys
sys.path.insert(0, '.')

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from src.models.model_tracker import (
    ModelIteration,
    LifecycleGraph,
    ModelTracker,
    ModelTrackerCache,
    get_model_tracker,
)


class TestModelIteration:
    """Tests for ModelIteration dataclass."""

    def test_creation(self):
        """Test ModelIteration creation."""
        iteration = ModelIteration(
            version_number=1,
            version_name="Initial",
            brier_score=0.22,
            accuracy=0.65,
            ece=0.04,
            sample_size=5000,
            is_active=True,
            trained_at=datetime(2026, 4, 15),
            metrics_history=[],
        )
        assert iteration.version_number == 1
        assert iteration.brier_score == 0.22
        assert iteration.is_active is True


class TestLifecycleGraph:
    """Tests for LifecycleGraph dataclass."""

    def test_creation(self):
        """Test LifecycleGraph creation."""
        iterations = [
            ModelIteration(1, None, 0.22, 0.65, 0.04, 5000, True, datetime.utcnow(), [])
        ]
        retrain_events = [{"reason": "Drift detected", "drift_score": 0.07}]

        graph = LifecycleGraph(
            market="btts",
            iterations=iterations,
            retrain_events=retrain_events,
            current_brier=0.22,
            baseline_brier=0.22,
            drift_score=0.0,
            overall_trend="stable",
        )

        assert graph.market == "btts"
        assert len(graph.iterations) == 1
        assert graph.overall_trend == "stable"


class TestModelTracker:
    """Tests for ModelTracker class."""

    def test_creation(self):
        """Test tracker creation with market."""
        tracker = ModelTracker(market="btts")
        assert tracker.market == "btts"

    def test_compute_baseline_insufficient_data(self):
        """Test baseline computation with few iterations."""
        tracker = ModelTracker(market="btts")
        tracker._compute_baseline([])
        tracker._compute_baseline([MagicMock(), MagicMock()])

    def test_compute_trend_insufficient_data(self):
        """Test trend computation with few iterations."""
        tracker = ModelTracker(market="btts")
        result = tracker._compute_trend([])
        assert result == "insufficient_data"

        result = tracker._compute_trend([MagicMock() for _ in range(3)])
        assert result == "insufficient_data"

    def test_compute_trend_stable(self):
        """Test trend computation for stable performance."""
        tracker = ModelTracker(market="btts")
        iterations = [
            ModelIteration(1, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.221, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(4, None, 0.219, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(5, None, 0.22, 0.65, 0.04, 5000, True, datetime.utcnow(), []),
        ]
        result = tracker._compute_trend(iterations)
        assert result == "stable"

    def test_compute_trend_improving(self):
        """Test trend computation for improving performance.

        Note: iterations are ordered DESC by version_number (newest first).
        So iterations[0] = oldest (v1), iterations[-1] = newest (v5).
        """
        tracker = ModelTracker(market="btts")
        iterations = [
            ModelIteration(5, None, 0.20, 0.68, 0.03, 5000, True, datetime.utcnow(), []),
            ModelIteration(4, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.23, 0.63, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.24, 0.62, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(1, None, 0.25, 0.60, 0.05, 5000, False, datetime.utcnow(), []),
        ]
        result = tracker._compute_trend(iterations)
        assert result == "improving"

    def test_compute_trend_degrading(self):
        """Test trend computation for degrading performance.

        Note: iterations are ordered DESC by version_number (newest first).
        So iterations[0] = oldest (v1), iterations[-1] = newest (v5).
        """
        tracker = ModelTracker(market="btts")
        iterations = [
            ModelIteration(5, None, 0.27, 0.58, 0.06, 5000, True, datetime.utcnow(), []),
            ModelIteration(4, None, 0.25, 0.60, 0.05, 5000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.23, 0.63, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(1, None, 0.20, 0.68, 0.03, 5000, False, datetime.utcnow(), []),
        ]
        result = tracker._compute_trend(iterations)
        assert result == "degrading"


class TestModelTrackerCache:
    """Tests for ModelTrackerCache."""

    def test_creation(self):
        """Test cache creation."""
        cache = ModelTrackerCache()
        assert cache._trackers == {}

    def test_get_tracker_creates_new(self):
        """Test get_tracker creates tracker for new market."""
        cache = ModelTrackerCache()
        tracker = cache.get_tracker("btts")
        assert tracker is not None
        assert tracker.market == "btts"

    def test_get_tracker_returns_same(self):
        """Test get_tracker returns same instance."""
        cache = ModelTrackerCache()
        tracker1 = cache.get_tracker("btts")
        tracker2 = cache.get_tracker("btts")
        assert tracker1 is tracker2

    def test_get_tracker_different_markets(self):
        """Test get_tracker creates separate trackers per market."""
        cache = ModelTrackerCache()
        btts = cache.get_tracker("btts")
        ou25 = cache.get_tracker("ou25")
        assert btts is not ou25
        assert btts.market == "btts"
        assert ou25.market == "ou25"


class TestGlobalTracker:
    """Tests for global tracker function."""

    def test_get_model_tracker(self):
        """Test get_model_tracker convenience function."""
        tracker = get_model_tracker("btts")
        assert tracker.market == "btts"
