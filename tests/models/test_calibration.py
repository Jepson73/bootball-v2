"""
tests/models/test_calibration.py

Tests for probability calibration using isotonic regression.
"""
import sys
sys.path.insert(0, '.')

import pytest
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.models.calibrator import (
    CalibrationResult,
    MarketCalibrator,
    CalibrationCache,
    get_calibration_cache,
    calibrate_prediction,
)

SKLEARN_AVAILABLE = False
try:
    from sklearn.isotonic import IsotonicRegression
    SKLEARN_AVAILABLE = True
except ImportError:
    pass


class TestCalibrationResult:
    """Tests for CalibrationResult dataclass."""

    def test_creation(self):
        """Test CalibrationResult creation with all fields."""
        result = CalibrationResult(
            original_prob=0.7,
            calibrated_prob=0.65,
            confidence_low=0.55,
            confidence_high=0.75,
            sample_size=1000,
        )
        assert result.original_prob == 0.7
        assert result.calibrated_prob == 0.65
        assert result.confidence_low == 0.55
        assert result.confidence_high == 0.75
        assert result.sample_size == 1000

    def test_defaults(self):
        """Test CalibrationResult with minimal fields."""
        result = CalibrationResult(
            original_prob=0.5,
            calibrated_prob=0.5,
            confidence_low=0.5,
            confidence_high=0.5,
            sample_size=0,
        )
        assert result.original_prob == 0.5
        assert result.sample_size == 0


class TestMarketCalibrator:
    """Tests for MarketCalibrator class."""

    def test_creation(self):
        """Test calibrator creation with market name."""
        calibrator = MarketCalibrator(market="btts")
        assert calibrator.market == "btts"
        assert calibrator.isotonic is None
        assert calibrator.sample_size == 0
        assert calibrator.calibrated_at is None
        assert calibrator.brier_score is None
        assert calibrator.ece is None

    def test_is_fresh_false_when_not_calibrated(self):
        """Test is_fresh returns False when no calibration done."""
        calibrator = MarketCalibrator(market="btts")
        assert calibrator.is_fresh() is False

    def test_is_fresh_false_when_old(self):
        """Test is_fresh returns False when calibration is old."""
        calibrator = MarketCalibrator(market="btts")
        calibrator.calibrated_at = datetime.utcnow() - timedelta(days=10)
        assert calibrator.is_fresh(max_age_days=7) is False

    def test_is_fresh_true_when_recent(self):
        """Test is_fresh returns True when calibration is recent."""
        calibrator = MarketCalibrator(market="btts")
        calibrator.calibrated_at = datetime.utcnow() - timedelta(days=3)
        assert calibrator.is_fresh(max_age_days=7) is True

    def test_calibrate_returns_unchanged_when_not_fitted(self):
        """Test calibrate() returns original prob when not fitted."""
        calibrator = MarketCalibrator(market="btts")
        result = calibrator.calibrate(0.7)

        assert result.original_prob == 0.7
        assert result.calibrated_prob == 0.7
        assert result.confidence_low == 0.7
        assert result.confidence_high == 0.7
        assert result.sample_size == 0

    def test_calibrate_batch_returns_unchanged_when_not_fitted(self):
        """Test calibrate_batch() returns original probs when not fitted."""
        calibrator = MarketCalibrator(market="btts")
        probs = np.array([0.3, 0.5, 0.7])
        result = calibrator.calibrate_batch(probs)

        np.testing.assert_array_equal(result, probs)

    def test_get_stats(self):
        """Test get_stats() returns expected dict."""
        calibrator = MarketCalibrator(market="btts")
        calibrator.sample_size = 5000
        calibrator.calibrated_at = datetime(2026, 4, 15, 12, 0, 0)
        calibrator.brier_score = 0.22
        calibrator.ece = 0.04

        stats = calibrator.get_stats()

        assert stats["market"] == "btts"
        assert stats["sample_size"] == 5000
        assert stats["calibrated_at"] == "2026-04-15T12:00:00"
        assert stats["brier_score"] == 0.22
        assert stats["ece"] == 0.04
        assert stats["is_fresh"] is True


class TestMarketCalibratorWithSklearn:
    """Tests for MarketCalibrator with sklearn available (mocked)."""

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_fit_with_insufficient_samples(self):
        """Test fit() logs warning when too few samples."""
        calibrator = MarketCalibrator(market="btts")
        raw_probs = np.array([0.5, 0.6, 0.7])
        outcomes = np.array([0, 1, 0])

        with patch('src.models.calibrator.logger') as mock_logger:
            calibrator.fit(raw_probs, outcomes)
            mock_logger.warning.assert_called()
            assert "Too few samples" in mock_logger.warning.call_args[0][0]

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_fit_success(self):
        """Test fit() successfully fits isotonic regression."""
        np.random.seed(42)
        n = 500
        raw_probs = np.random.uniform(0.2, 0.8, n)
        outcomes = (raw_probs + np.random.normal(0, 0.1, n) > 0.5).astype(float)

        calibrator = MarketCalibrator(market="btts")
        result = calibrator.fit(raw_probs, outcomes, n_bins=10)

        assert result is calibrator
        assert calibrator.sample_size == n
        assert calibrator.isotonic is not None
        assert calibrator.calibrated_at is not None
        assert calibrator.brier_score is not None
        assert calibrator.ece is not None
        assert 0 <= calibrator.ece <= 1

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_calibrate_clamps_output(self):
        """Test calibrate() clips output to [0.01, 0.99]."""
        np.random.seed(42)
        n = 500
        raw_probs = np.random.uniform(0.1, 0.9, n)
        outcomes = (raw_probs > 0.5).astype(float)

        calibrator = MarketCalibrator(market="btts")
        calibrator.fit(raw_probs, outcomes)

        result = calibrator.calibrate(0.01)
        assert result.calibrated_prob >= 0.01

        result = calibrator.calibrate(0.99)
        assert result.calibrated_prob <= 0.99

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_calibrate_batch(self):
        """Test calibrate_batch() calibrates multiple probabilities."""
        np.random.seed(42)
        n = 500
        raw_probs = np.random.uniform(0.2, 0.8, n)
        outcomes = (raw_probs + np.random.normal(0, 0.1, n) > 0.5).astype(float)

        calibrator = MarketCalibrator(market="btts")
        calibrator.fit(raw_probs, outcomes)

        test_probs = np.array([0.3, 0.5, 0.7])
        calibrated = calibrator.calibrate_batch(test_probs)

        assert len(calibrated) == 3
        assert all(0.01 <= p <= 0.99 for p in calibrated)

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_confidence_interval_narrower_with_more_data(self):
        """Test confidence interval narrows with more training data."""
        np.random.seed(42)

        calibrator_small = MarketCalibrator(market="btts")
        raw_probs = np.random.uniform(0.3, 0.7, 500)
        outcomes = (raw_probs > 0.5).astype(float)
        calibrator_small.fit(raw_probs, outcomes)

        calibrator_large = MarketCalibrator(market="btts")
        raw_probs = np.random.uniform(0.3, 0.7, 15000)
        outcomes = (raw_probs > 0.5).astype(float)
        calibrator_large.fit(raw_probs, outcomes)

        result_small = calibrator_small.calibrate(0.5)
        result_large = calibrator_large.calibrate(0.5)

        small_width = result_small.confidence_high - result_small.confidence_low
        large_width = result_large.confidence_high - result_large.confidence_low

        assert large_width < small_width


class TestBrierScore:
    """Tests for Brier score calculation."""

    def test_brier_score_perfect(self):
        """Test Brier score is 0 when predictions are perfect."""
        calibrator = MarketCalibrator(market="btts")
        probs = np.array([1.0, 0.0, 1.0, 0.0])
        outcomes = np.array([1.0, 0.0, 1.0, 0.0])

        score = calibrator._brier_score(probs, outcomes)
        assert score == 0.0

    def test_brier_score_worst(self):
        """Test Brier score is 1.0 when always completely wrong."""
        calibrator = MarketCalibrator(market="btts")
        probs = np.array([0.0, 1.0, 0.0, 1.0])
        outcomes = np.array([1.0, 0.0, 1.0, 0.0])

        score = calibrator._brier_score(probs, outcomes)
        assert score == 1.0

    def test_brier_score_value(self):
        """Test Brier score calculation."""
        calibrator = MarketCalibrator(market="btts")
        probs = np.array([0.6, 0.4])
        outcomes = np.array([1.0, 0.0])

        score = calibrator._brier_score(probs, outcomes)
        expected = ((0.6 - 1.0) ** 2 + (0.4 - 0.0) ** 2) / 2
        assert abs(score - expected) < 0.0001


class TestECE:
    """Tests for Expected Calibration Error calculation."""

    def test_ece_perfect_calibration(self):
        """Test ECE approaches 0 with many fine bins and properly generated outcomes."""
        np.random.seed(42)
        n = 10000
        probs = np.random.uniform(0.1, 0.9, n)
        outcomes = (np.random.random(n) < probs).astype(float)

        calibrator = MarketCalibrator(market="btts")
        ece = calibrator._ece(probs, outcomes, n_bins=100)
        assert ece < 0.04

    def test_ece_value(self):
        """Test ECE calculation."""
        calibrator = MarketCalibrator(market="btts")
        probs = np.array([0.1, 0.1, 0.9, 0.9])
        outcomes = np.array([0.0, 0.0, 0.0, 0.0])

        ece = calibrator._ece(probs, outcomes, n_bins=2)
        assert ece > 0


class TestSaveLoad:
    """Tests for calibrator save/load functionality."""

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not available")
    def test_save_and_load(self, tmp_path):
        """Test calibrator can be saved and loaded."""
        np.random.seed(42)
        n = 500
        raw_probs = np.random.uniform(0.3, 0.7, n)
        outcomes = (raw_probs > 0.5).astype(float)

        calibrator = MarketCalibrator(market="btts")
        calibrator.fit(raw_probs, outcomes)

        path = tmp_path / "test_calibrator.pkl"
        calibrator.save(path)
        assert path.exists()

        loaded = MarketCalibrator.load(path)

        assert loaded.market == "btts"
        assert loaded.sample_size == n
        assert loaded.brier_score == calibrator.brier_score
        assert loaded.ece == calibrator.ece

        result_original = calibrator.calibrate(0.5)
        result_loaded = loaded.calibrate(0.5)
        assert result_original.calibrated_prob == result_loaded.calibrated_prob


class TestCalibrationCache:
    """Tests for CalibrationCache class."""

    def test_cache_creation(self):
        """Test CalibrationCache initializes correctly."""
        cache = CalibrationCache()
        assert cache._calibrators == {}

    def test_get_calibrator_returns_none_when_not_cached(self):
        """Test get_calibrator returns None when not in cache."""
        cache = CalibrationCache()
        result = cache.get_calibrator("btts")
        assert result is None

    def test_calibrate_prediction_returns_unchanged_when_no_calibrator(self):
        """Test calibrate_prediction returns original when no calibrator."""
        cache = CalibrationCache()
        result = cache.calibrate_prediction("btts", 0.7)

        assert result.original_prob == 0.7
        assert result.calibrated_prob == 0.7
        assert result.sample_size == 0

    def test_global_cache(self):
        """Test get_calibration_cache returns same instance."""
        cache1 = get_calibration_cache()
        cache2 = get_calibration_cache()
        assert cache1 is cache2


class TestCalibratePrediction:
    """Tests for calibrate_prediction convenience function."""

    def test_returns_unchanged_when_no_calibrator(self):
        """Test calibrate_prediction returns original when no calibrator."""
        result = calibrate_prediction("btts", 0.7)

        assert result.original_prob == 0.7
        assert result.calibrated_prob == 0.7
        assert result.sample_size == 0
