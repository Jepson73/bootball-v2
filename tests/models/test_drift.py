"""
tests/models/test_drift.py

Tests for drift detection system.
"""
import sys
sys.path.insert(0, '.')

import pytest
import numpy as np

from src.models.drift_detector import (
    DriftResult,
    DriftDetector,
    DriftMonitor,
    get_drift_monitor,
    check_drift,
)


class TestDriftResult:
    """Tests for DriftResult dataclass."""

    def test_creation(self):
        """Test DriftResult creation with all fields."""
        result = DriftResult(
            market="btts",
            current_brier=0.22,
            baseline_brier=0.20,
            drift_detected=True,
            drift_score=0.02,
            drift_direction="worse",
            severity="minor",
            recommendation="Monitor closely",
            confidence=0.8,
        )
        assert result.market == "btts"
        assert result.current_brier == 0.22
        assert result.drift_detected is True
        assert result.drift_direction == "worse"


class TestDriftDetector:
    """Tests for DriftDetector class."""

    def test_creation(self):
        """Test detector creation with market name."""
        detector = DriftDetector(market="btts")
        assert detector.market == "btts"
        assert detector.alert_threshold == 0.05
        assert len(detector._recent) == 0
        assert len(detector._baseline) == 0

    def test_custom_threshold(self):
        """Test detector uses market-specific threshold."""
        detector = DriftDetector(market="h2h")
        assert detector.alert_threshold == 0.06

    def test_unknown_market_default_threshold(self):
        """Test unknown market uses default threshold."""
        detector = DriftDetector(market="unknown")
        assert detector.alert_threshold == 0.05

    def test_add_prediction(self):
        """Test adding prediction outcomes."""
        detector = DriftDetector(market="btts")

        detector.add_prediction(0.7, 1)
        detector.add_prediction(0.7, 0)
        detector.add_prediction(0.3, 0)

        assert len(detector._recent) == 3

    def test_set_baseline(self):
        """Test setting baseline Brier scores."""
        detector = DriftDetector(market="btts")
        baseline = [0.20, 0.22, 0.21, 0.23, 0.20]

        detector.set_baseline(baseline)

        assert len(detector._baseline) == 5
        assert detector._baseline == baseline

    def test_detect_drift_insufficient_recent(self):
        """Test returns unknown when recent data insufficient."""
        detector = DriftDetector(market="btts")
        detector.set_baseline([0.20] * 100)

        result = detector.detect_drift()

        assert result.drift_detected is False
        assert result.severity == "unknown"
        assert "Insufficient data" in result.recommendation

    def test_detect_drift_insufficient_baseline(self):
        """Test returns unknown when baseline insufficient."""
        detector = DriftDetector(market="btts")
        detector._recent = [0.20] * 100

        result = detector.detect_drift()

        assert result.drift_detected is False
        assert result.severity == "unknown"
        assert "Insufficient baseline" in result.recommendation

    def test_detect_drift_no_drift(self):
        """Test no drift detected when performance stable."""
        detector = DriftDetector(market="btts")
        detector._baseline = [0.20] * 200
        detector._recent = [0.20] * 100

        result = detector.detect_drift()

        assert result.drift_detected is False
        assert result.confidence > 0.5

    def test_detect_drift_positive(self):
        """Test drift detected when Brier score worsens."""
        detector = DriftDetector(market="btts")
        detector._baseline = [0.20] * 200
        detector._recent = [0.27] * 100

        result = detector.detect_drift()

        assert result.drift_detected is True
        assert result.drift_direction == "worse"
        assert result.drift_score > 0
        assert result.current_brier > result.baseline_brier

    def test_detect_drift_negative(self):
        """Test drift detected when Brier score improves significantly (>0.08)."""
        detector = DriftDetector(market="btts")
        detector._baseline = [0.25] * 200
        detector._recent = [0.14] * 100

        result = detector.detect_drift()

        assert result.drift_detected is True
        assert result.drift_direction == "better"
        assert result.drift_score < 0

    def test_severity_levels(self):
        """Test severity classification."""
        detector = DriftDetector(market="btts")
        detector._baseline = [0.20] * 200

        detector._recent = [0.21] * 100
        result = detector.detect_drift()
        assert result.severity == "none"

        detector._recent = [0.26] * 100
        result = detector.detect_drift()
        assert result.severity == "moderate"

        detector._recent = [0.31] * 100
        result = detector.detect_drift()
        assert result.severity == "major"

        detector._recent = [0.41] * 100
        result = detector.detect_drift()
        assert result.severity == "critical"

    def test_get_stats(self):
        """Test get_stats returns expected dict."""
        detector = DriftDetector(market="btts")
        detector._baseline = [0.20] * 100
        detector._recent = [0.22] * 50

        stats = detector.get_stats()

        assert stats["market"] == "btts"
        assert stats["baseline_count"] == 100
        assert stats["recent_count"] == 50
        assert abs(stats["baseline_mean"] - 0.20) < 0.001
        assert abs(stats["recent_mean"] - 0.22) < 0.001

    def test_reset_recent(self):
        """Test reset_recent clears recent data."""
        detector = DriftDetector(market="btts")
        detector._recent = [0.20] * 50

        detector.reset_recent()

        assert len(detector._recent) == 0


class TestDriftMonitor:
    """Tests for DriftMonitor class."""

    def test_creation(self):
        """Test monitor creation."""
        monitor = DriftMonitor()
        assert monitor._detectors == {}

    def test_get_detector_creates_new(self):
        """Test get_detector creates detector for new market."""
        monitor = DriftMonitor()
        detector = monitor.get_detector("btts")

        assert detector is not None
        assert detector.market == "btts"

    def test_get_detector_returns_same(self):
        """Test get_detector returns same instance."""
        monitor = DriftMonitor()
        detector1 = monitor.get_detector("btts")
        detector2 = monitor.get_detector("btts")

        assert detector1 is detector2

    def test_get_detector_different_markets(self):
        """Test get_detector creates separate detectors per market."""
        monitor = DriftMonitor()
        btts = monitor.get_detector("btts")
        ou25 = monitor.get_detector("ou25")

        assert btts is not ou25
        assert btts.market == "btts"
        assert ou25.market == "ou25"

    def test_check_all_markets_empty(self):
        """Test check_all_markets with no detectors."""
        monitor = DriftMonitor()
        results = monitor.check_all_markets()

        assert results == []

    def test_check_all_markets_with_data(self):
        """Test check_all_markets returns results for all markets."""
        monitor = DriftMonitor()
        btts = monitor.get_detector("btts")
        btts._baseline = [0.20] * 200
        btts._recent = [0.22] * 100

        ou25 = monitor.get_detector("ou25")
        ou25._baseline = [0.22] * 200
        ou25._recent = [0.21] * 100

        results = monitor.check_all_markets()

        assert len(results) == 2
        markets = {r.market for r in results}
        assert markets == {"btts", "ou25"}

    def test_get_alerts_filters_non_drift(self):
        """Test get_alerts only returns drifting markets."""
        monitor = DriftMonitor()

        btts = monitor.get_detector("btts")
        btts._baseline = [0.20] * 200
        btts._recent = [0.20] * 100

        ou25 = monitor.get_detector("ou25")
        ou25._baseline = [0.22] * 200
        ou25._recent = [0.30] * 100

        alerts = monitor.get_alerts()

        assert len(alerts) == 1
        assert alerts[0].market == "ou25"
        assert alerts[0].drift_detected is True


class TestGlobalMonitor:
    """Tests for global drift monitor."""

    def test_get_drift_monitor_returns_same_instance(self):
        """Test get_drift_monitor returns singleton."""
        monitor1 = get_drift_monitor()
        monitor2 = get_drift_monitor()

        assert monitor1 is monitor2

    def test_check_drift_convenience(self):
        """Test check_drift convenience function."""
        result = check_drift("btts")

        assert result.market == "btts"
        assert result.confidence == 0.0
