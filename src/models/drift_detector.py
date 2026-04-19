"""
src/models/drift_detector.py

Drift detection for model monitoring.
Detects when model calibration degrades using Brier score tracking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DriftResult:
    """Result of drift detection analysis."""
    market: str
    current_brier: float
    baseline_brier: float
    drift_detected: bool
    drift_score: float
    drift_direction: str
    severity: str
    recommendation: str
    confidence: float


class DriftDetector:
    """Detects model drift by tracking Brier score over time.

    Drift detection compares recent performance against baseline.
    When drift is detected, retraining is recommended.
    """

    ALERT_THRESHOLDS = {
        "btts": 0.05,
        "ou25": 0.05,
        "ou15": 0.05,
        "h2h": 0.06,
    }

    SEVERITY_LEVELS = {
        "minor": 0.03,
        "moderate": 0.05,
        "major": 0.10,
        "critical": 0.20,
    }

    def __init__(self, market: str):
        self.market = market
        self.alert_threshold = self.ALERT_THRESHOLDS.get(market, 0.05)
        self._baseline: list[float] = []
        self._recent: list[float] = []
        self._baseline_period_days = 30
        self._recent_period_days = 7

    def add_prediction(self, prob: float, outcome: int) -> None:
        """Add a prediction outcome for tracking.

        Args:
            prob: The predicted probability (0-1)
            outcome: The actual outcome (0 or 1)
        """
        brier = (prob - outcome) ** 2
        self._recent.append(brier)

    def set_baseline(self, brier_scores: list[float]) -> None:
        """Set the baseline Brier scores for comparison.

        Args:
            brier_scores: Historical Brier scores representing stable performance
        """
        self._baseline = list(brier_scores)

    def detect_drift(self) -> DriftResult:
        """Analyze recent performance vs baseline for drift.

        Returns:
            DriftResult with drift analysis
        """
        if len(self._recent) < 30:
            return DriftResult(
                market=self.market,
                current_brier=0.0,
                baseline_brier=0.0,
                drift_detected=False,
                drift_score=0.0,
                drift_direction="unknown",
                severity="unknown",
                recommendation="Insufficient data for drift detection",
                confidence=0.0,
            )

        if len(self._baseline) < 100:
            return DriftResult(
                market=self.market,
                current_brier=self._compute_mean(self._recent),
                baseline_brier=0.0,
                drift_detected=False,
                drift_score=0.0,
                drift_direction="unknown",
                severity="unknown",
                recommendation="Insufficient baseline data",
                confidence=0.0,
            )

        current_brier = self._compute_mean(self._recent)
        baseline_brier = self._compute_mean(self._baseline)
        baseline_std = self._compute_std(self._baseline)

        drift_score = current_brier - baseline_brier

        drift_detected = abs(drift_score) > self.alert_threshold
        drift_direction = "worse" if drift_score > 0 else "better"

        severity = self._compute_severity(abs(drift_score))
        confidence = self._compute_confidence()

        recommendation = self._get_recommendation(
            drift_detected, drift_direction, severity, confidence
        )

        return DriftResult(
            market=self.market,
            current_brier=current_brier,
            baseline_brier=baseline_brier,
            drift_detected=drift_detected,
            drift_score=drift_score,
            drift_direction=drift_direction,
            severity=severity,
            recommendation=recommendation,
            confidence=confidence,
        )

    def _compute_mean(self, scores: list[float]) -> float:
        """Compute mean of Brier scores."""
        if not scores:
            return 0.0
        return float(np.mean(scores))

    def _compute_std(self, scores: list[float]) -> float:
        """Compute standard deviation of Brier scores."""
        if len(scores) < 2:
            return 0.0
        return float(np.std(scores))

    def _compute_severity(self, drift_magnitude: float) -> str:
        """Map drift magnitude to severity level."""
        if drift_magnitude >= self.SEVERITY_LEVELS["critical"]:
            return "critical"
        elif drift_magnitude >= self.SEVERITY_LEVELS["major"]:
            return "major"
        elif drift_magnitude >= self.SEVERITY_LEVELS["moderate"]:
            return "moderate"
        elif drift_magnitude >= self.SEVERITY_LEVELS["minor"]:
            return "minor"
        return "none"

    def _compute_confidence(self) -> float:
        """Compute confidence in drift detection based on sample sizes."""
        baseline_confidence = min(len(self._baseline) / 500, 1.0)
        recent_confidence = min(len(self._recent) / 100, 1.0)
        return float((baseline_confidence + recent_confidence) / 2)

    def _get_recommendation(
        self,
        drift_detected: bool,
        drift_direction: str,
        severity: str,
        confidence: float,
    ) -> str:
        """Generate recommendation based on drift analysis."""
        if not drift_detected:
            return "Model performing within normal parameters"

        if confidence < 0.5:
            return "Collect more data before taking action"

        if severity == "critical":
            return f"URGENT: Immediate retraining recommended. {severity.title()} drift ({drift_direction})"
        elif severity == "major":
            return f"Retrain model soon. {severity.title()} drift ({drift_direction}) detected"
        elif severity == "moderate":
            return f"Monitor closely. {severity.title()} drift ({drift_direction}) detected"
        else:
            return f"Minor drift ({drift_direction}). Continue monitoring"

    def get_stats(self) -> dict[str, Any]:
        """Get current drift detector statistics."""
        return {
            "market": self.market,
            "alert_threshold": self.alert_threshold,
            "baseline_count": len(self._baseline),
            "recent_count": len(self._recent),
            "baseline_mean": self._compute_mean(self._baseline),
            "recent_mean": self._compute_mean(self._recent),
            "baseline_std": self._compute_std(self._baseline),
        }

    def reset_recent(self) -> None:
        """Clear recent predictions (call after analyzing/retraining)."""
        self._recent = []

    def prune_old_predictions(self, max_age_days: int = 7) -> None:
        """Remove old predictions beyond the tracking window."""
        pass


class DriftMonitor:
    """Manages drift detection across all markets."""

    def __init__(self):
        self._detectors: dict[str, DriftDetector] = {}

    def get_detector(self, market: str) -> DriftDetector:
        """Get or create detector for market."""
        if market not in self._detectors:
            self._detectors[market] = DriftDetector(market)
        return self._detectors[market]

    def check_all_markets(self) -> list[DriftResult]:
        """Run drift detection on all markets with data."""
        results = []
        for market, detector in self._detectors.items():
            if len(detector._recent) >= 30:
                result = detector.detect_drift()
                results.append(result)
        return results

    def get_alerts(self) -> list[DriftResult]:
        """Get only markets with drift detected."""
        all_results = self.check_all_markets()
        return [r for r in all_results if r.drift_detected]


_drift_monitor: DriftMonitor | None = None


def get_drift_monitor() -> DriftMonitor:
    """Get global drift monitor."""
    global _drift_monitor
    if _drift_monitor is None:
        _drift_monitor = DriftMonitor()
    return _drift_monitor


def check_drift(market: str) -> DriftResult:
    """Convenience function to check drift for a market."""
    monitor = get_drift_monitor()
    detector = monitor.get_detector(market)
    return detector.detect_drift()
