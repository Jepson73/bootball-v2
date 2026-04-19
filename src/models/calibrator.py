"""
src/models/calibrator.py

Probability calibration using isotonic regression.
Improves model reliability and proper EV calculation.

Research shows: Calibration-optimized models yield +34.69% ROI vs -35.17% for accuracy.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CalibrationResult:
    """Result of calibration."""
    original_prob: float
    calibrated_prob: float
    confidence_low: float
    confidence_high: float
    sample_size: int


class MarketCalibrator:
    """Isotonic regression calibrator for a specific market.

    Calibration maps raw model probabilities to well-calibrated probabilities
    where 60% predictions actually win 60% of the time.
    """

    def __init__(self, market: str):
        self.market = market
        self.isotonic = None
        self.sample_size = 0
        self.calibrated_at: datetime | None = None
        self.brier_score: float | None = None
        self.ece: float | None = None

    def fit(
        self,
        raw_probs: np.ndarray,
        outcomes: np.ndarray,
        n_bins: int = 10,
    ) -> "MarketCalibrator":
        """Fit calibrator on validation data.

        Args:
            raw_probs: Raw model probabilities (0-1)
            outcomes: Actual outcomes (0 or 1)
            n_bins: Number of bins for ECE calculation

        Returns:
            self for chaining
        """
        try:
            from sklearn.isotonic import IsotonicRegression
        except ImportError:
            logger.warning("sklearn not available, calibration disabled")
            return self

        if len(raw_probs) < 100:
            logger.warning(f"Too few samples ({len(raw_probs)}) for calibration")
            return self

        self.isotonic = IsotonicRegression(out_of_bounds="clip")
        self.isotonic.fit(raw_probs, outcomes)
        self.sample_size = len(raw_probs)
        self.calibrated_at = datetime.utcnow()

        calibrated_probs = self.isotonic.predict(raw_probs)

        self.brier_score = self._brier_score(raw_probs, outcomes)
        self.ece = self._ece(raw_probs, outcomes, n_bins)

        logger.info(
            f"Calibrated {self.market}: "
            f"Brier={self.brier_score:.4f}, ECE={self.ece:.4f}, n={self.sample_size}"
        )

        return self

    def calibrate(self, raw_prob: float) -> CalibrationResult:
        """Calibrate a single probability.

        Args:
            raw_prob: Raw model probability (0-1)

        Returns:
            CalibrationResult with calibrated probability and confidence interval
        """
        if self.isotonic is None:
            return CalibrationResult(
                original_prob=raw_prob,
                calibrated_prob=raw_prob,
                confidence_low=raw_prob,
                confidence_high=raw_prob,
                sample_size=0,
            )

        calibrated = float(self.isotonic.predict([raw_prob])[0])
        calibrated = max(0.01, min(0.99, calibrated))

        confidence = self._get_confidence_interval(raw_prob)

        return CalibrationResult(
            original_prob=raw_prob,
            calibrated_prob=calibrated,
            confidence_low=max(0.01, calibrated - confidence),
            confidence_high=min(0.99, calibrated + confidence),
            sample_size=self.sample_size,
        )

    def calibrate_batch(self, raw_probs: np.ndarray) -> np.ndarray:
        """Calibrate multiple probabilities.

        Args:
            raw_probs: Array of raw probabilities

        Returns:
            Array of calibrated probabilities
        """
        if self.isotonic is None:
            return raw_probs

        calibrated = self.isotonic.predict(raw_probs)
        calibrated = np.clip(calibrated, 0.01, 0.99)
        return calibrated

    def _brier_score(self, probs: np.ndarray, outcomes: np.ndarray) -> float:
        """Calculate Brier score (lower is better).

        Brier = mean((probability - outcome)^2)

        Target: < 0.25 for 3-outcome markets, < 0.20 for 2-outcome
        """
        return float(np.mean((probs - outcomes) ** 2))

    def _ece(
        self,
        probs: np.ndarray,
        outcomes: np.ndarray,
        n_bins: int = 10,
    ) -> float:
        """Calculate Expected Calibration Error.

        ECE = sum(|accuracy - confidence| * weight) for each bin

        Target: < 0.05 (5% miscalibration)
        """
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0

        for i in range(n_bins):
            mask = (probs >= bin_edges[i]) & (probs < bin_edges[i + 1])
            if i == n_bins - 1:
                mask = (probs >= bin_edges[i]) & (probs <= bin_edges[i + 1])

            if np.sum(mask) == 0:
                continue

            bin_accuracy = np.mean(outcomes[mask])
            bin_confidence = np.mean(probs[mask])
            bin_weight = np.sum(mask) / len(probs)

            ece += bin_weight * abs(bin_accuracy - bin_confidence)

        return float(ece)

    def _get_confidence_interval(self, prob: float) -> float:
        """Get confidence interval width based on sample size.

        More training data = narrower confidence interval.
        """
        if self.sample_size < 1000:
            return 0.20
        elif self.sample_size < 5000:
            return 0.15
        elif self.sample_size < 10000:
            return 0.10
        else:
            return 0.08

    def save(self, path: Path) -> None:
        """Save calibrator to disk."""
        data = {
            "isotonic": self.isotonic,
            "sample_size": self.sample_size,
            "calibrated_at": self.calibrated_at,
            "brier_score": self.brier_score,
            "ece": self.ece,
            "market": self.market,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)
        logger.info(f"Saved calibrator to {path}")

    @classmethod
    def load(cls, path: Path) -> "MarketCalibrator":
        """Load calibrator from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)

        calibrator = cls(data["market"])
        calibrator.isotonic = data["isotonic"]
        calibrator.sample_size = data["sample_size"]
        calibrator.calibrated_at = data["calibrated_at"]
        calibrator.brier_score = data["brier_score"]
        calibrator.ece = data["ece"]

        logger.info(f"Loaded calibrator from {path}")
        return calibrator

    def is_fresh(self, max_age_days: int = 7) -> bool:
        """Check if calibrator is recent enough to use."""
        if self.calibrated_at is None:
            return False
        age = (datetime.utcnow() - self.calibrated_at).days
        return age < max_age_days

    def get_stats(self) -> dict[str, Any]:
        """Get calibration statistics."""
        return {
            "market": self.market,
            "sample_size": self.sample_size,
            "calibrated_at": self.calibrated_at.isoformat() if self.calibrated_at else None,
            "brier_score": self.brier_score,
            "ece": self.ece,
            "is_fresh": self.is_fresh(),
        }


class CalibrationCache:
    """Manages calibrators for all markets."""

    CACHE_DIR = Path("data/models/calibrators")

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._calibrators: dict[str, MarketCalibrator] = {}

    def get_calibrator(self, market: str) -> MarketCalibrator | None:
        """Get calibrator for market, loading from disk if available."""
        if market in self._calibrators:
            return self._calibrators[market]

        path = self.CACHE_DIR / f"calibrator_{market}.pkl"
        if path.exists():
            try:
                calibrator = MarketCalibrator.load(path)
                if calibrator.is_fresh():
                    self._calibrators[market] = calibrator
                    return calibrator
            except Exception as e:
                logger.warning(f"Failed to load calibrator: {e}")

        return None

    def save_calibrator(self, calibrator: MarketCalibrator) -> None:
        """Save calibrator to disk and cache."""
        path = self.CACHE_DIR / f"calibrator_{calibrator.market}.pkl"
        calibrator.save(path)
        self._calibrators[calibrator.market] = calibrator

    def calibrate_prediction(
        self,
        market: str,
        raw_prob: float,
    ) -> CalibrationResult:
        """Calibrate a prediction if calibrator exists."""
        calibrator = self.get_calibrator(market)
        if calibrator is None:
            return CalibrationResult(
                original_prob=raw_prob,
                calibrated_prob=raw_prob,
                confidence_low=raw_prob,
                confidence_high=raw_prob,
                sample_size=0,
            )
        return calibrator.calibrate(raw_prob)


_calibration_cache: CalibrationCache | None = None


def get_calibration_cache() -> CalibrationCache:
    """Get global calibration cache."""
    global _calibration_cache
    if _calibration_cache is None:
        _calibration_cache = CalibrationCache()
    return _calibration_cache


def calibrate_prediction(
    market: str,
    raw_prob: float,
) -> CalibrationResult:
    """Convenience function to calibrate a single prediction."""
    return get_calibration_cache().calibrate_prediction(market, raw_prob)
