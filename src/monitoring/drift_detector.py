"""
Drift Detector Engine - Real-time monitoring for anomaly and drift detection.

Monitors event stream for:
- Model performance drift
- Market instability shifts  
- ROI anomalies
- Behavioral patterns
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from src.state.models import SystemState

logger = logging.getLogger(__name__)


class DriftDetector:
    """
    Analyze events to detect drift and anomalies.
    
    Does NOT modify any decisions - only observes and emits alerts.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        
        # Thresholds (can be overridden by config)
        self.drift_threshold = self.config.get("drift_alert_threshold", 0.15)
        self.roi_drop_threshold = self.config.get("roi_drop_threshold", 5.0)
        self.volatility_threshold = self.config.get("volatility_threshold", 2.0)
        self.market_shift_threshold = self.config.get("market_shift_sensitivity", 0.20)
        
        # Historical data for comparison
        self.baseline_roi = None
        self.baseline_metrics = {}
    
    def analyze_event_window(self, events: list[dict]) -> dict:
        """
        Analyze a window of events for drift and anomalies.
        
        Args:
            events: List of events to analyze
            
        Returns:
            Dict with all detection results
        """
        if not events:
            return {"error": "No events to analyze"}
        
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_count": len(events),
            "detections": []
        }
        
        # Run all detectors
        model_drift = self.detect_model_drift(events)
        if model_drift.get("severity") != "none":
            results["detections"].append(model_drift)
        
        market_shift = self.detect_market_shift(events)
        if market_shift.get("severity") != "none":
            results["detections"].append(market_shift)
        
        roi_anomaly = self.detect_roi_anomaly(events)
        if roi_anomaly.get("severity") != "none":
            results["detections"].append(roi_anomaly)
        
        # Overall health assessment
        if results["detections"]:
            results["health_status"] = "degraded"
            results["alert_count"] = len(results["detections"])
        else:
            results["health_status"] = "healthy"
            results["alert_count"] = 0
        
        return results
    
    def detect_model_drift(self, events: list[dict]) -> dict:
        """
        Detect model performance drift.
        
        Analyzes calibration and prediction quality over events.
        """
        # Collect prediction events
        predictions = []
        settled = []
        
        for event in events:
            if event.get("event_type") == "predictions_generated":
                predictions.append(event)
            elif event.get("event_type") in ["bet_settled", "bets_settled"]:
                settled.append(event)
        
        if not predictions:
            return {"type": "model_drift", "severity": "none", "details": {}}
        
        # Calculate drift score based on settled outcomes
        drift_score = 0.0
        calibration_errors = []
        
        for settle in settled:
            payload = settle.get("payload", {})
            wins = payload.get("wins", 0)
            losses = payload.get("losses", 0)
            total = wins + losses
            
            if total > 0:
                win_rate = wins / total
                # Expected ~50% for well-calibrated model
                error = abs(win_rate - 0.5)
                calibration_errors.append(error)
        
        if calibration_errors:
            avg_error = sum(calibration_errors) / len(calibration_errors)
            drift_score = min(1.0, avg_error * 2)  # Scale to 0-1
        
        # Determine severity
        if drift_score >= self.drift_threshold:
            severity = "high"
        elif drift_score >= self.drift_threshold * 0.7:
            severity = "medium"
        elif drift_score >= self.drift_threshold * 0.4:
            severity = "low"
        else:
            severity = "none"
        
        return {
            "type": "model_drift",
            "severity": severity,
            "score": drift_score,
            "details": {
                "prediction_count": len(predictions),
                "settled_count": len(settled),
                "avg_calibration_error": sum(calibration_errors) / len(calibration_errors) if calibration_errors else 0,
                "threshold": self.drift_threshold
            }
        }
    
    def detect_market_shift(self, events: list[dict]) -> dict:
        """
        Detect market stability shifts.
        
        Monitors profitability changes across markets (h2h, btts, ou25).
        """
        market_roi = defaultdict(list)
        market_bets = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        
        for event in events:
            if event.get("event_type") == "bets_generated":
                payload = event.get("payload", {})
                for bet in payload.get("bets", []):
                    market = bet.get("market", "unknown")
                    market_roi[market].append(bet.get("ev", 0))
            
            elif event.get("event_type") in ["bet_settled", "bets_settled"]:
                payload = event.get("payload", {})
                # This is simplified - real implementation would track per market
                market_bets["all"]["wins"] += payload.get("wins", 0)
                market_bets["all"]["losses"] += payload.get("losses", 0)
                market_bets["all"]["total"] += payload.get("settled_count", 0)
        
        # Calculate stability per market
        unstable_markets = []
        
        for market, ev_values in market_roi.items():
            if ev_values:
                avg_ev = sum(ev_values) / len(ev_values)
                # Check if EV significantly degraded
                if avg_ev < self.market_shift_threshold:
                    unstable_markets.append(market)
        
        # Determine severity
        if len(unstable_markets) >= 2:
            severity = "high"
        elif len(unstable_markets) == 1:
            severity = "medium"
        elif any(abs(ev) < 0.02 for ev in market_roi.get("all", [])):
            severity = "low"
        else:
            severity = "none"
        
        stability_score = 1.0 - (len(unstable_markets) * 0.25)
        
        return {
            "type": "market_shift",
            "severity": severity,
            "score": 1 - stability_score,
            "details": {
                "unstable_markets": unstable_markets,
                "stability_score": stability_score,
                "markets_analyzed": list(market_roi.keys())
            }
        }
    
    def detect_roi_anomaly(self, events: list[dict]) -> dict:
        """
        Detect ROI anomalies and degradation patterns.
        
        Monitors for sudden performance collapse or sustained underperformance.
        """
        # Extract ROI data from finished runs
        run_rois = []
        run_pnls = []
        
        for event in events:
            if event.get("event_type") == "run_finished":
                payload = event.get("payload", {})
                total_bets = payload.get("total_bets", 0)
                if total_bets > 0:
                    # Estimate ROI from total_ev (simplified)
                    ev = payload.get("total_ev", 0)
                    run_rois.append(ev)
                run_pnls.append(payload.get("total_pnl", 0))
        
        if len(run_rois) < 2:
            return {"type": "roi_anomaly", "severity": "none", "details": {}}
        
        # Calculate recent trend
        recent_roi = run_rois[-1] if run_rois else 0
        avg_roi = sum(run_rois) / len(run_rois)
        
        # Check for degradation
        roi_drop = avg_roi - recent_roi
        
        # Check for volatility (variance in ROI)
        if len(run_rois) > 1:
            mean = sum(run_rois) / len(run_rois)
            variance = sum((r - mean) ** 2 for r in run_rois) / len(run_rois)
            volatility = variance ** 0.5
        else:
            volatility = 0
        
        # Determine severity
        severity = "none"
        
        if roi_drop > self.roi_drop_threshold and volatility > self.volatility_threshold:
            severity = "high"
        elif roi_drop > self.roi_drop_threshold * 0.7:
            severity = "medium"
        elif volatility > self.volatility_threshold:
            severity = "low"
        
        return {
            "type": "roi_anomaly",
            "severity": severity,
            "score": min(1.0, (roi_drop / self.roi_drop_threshold)),
            "details": {
                "recent_roi": recent_roi,
                "avg_roi": avg_roi,
                "roi_drop": roi_drop,
                "volatility": volatility,
                "runs_analyzed": len(run_rois),
                "roi_threshold": self.roi_drop_threshold,
                "volatility_threshold": self.volatility_threshold
            }
        }
    
    def set_baseline(self, events: list[dict]) -> None:
        """
        Set baseline metrics for comparison.
        
        Args:
            events: Events to use as baseline
        """
        analysis = self.analyze_event_window(events)
        
        self.baseline_roi = analysis.get("details", {}).get("avg_roi", 0)
        
        # Store baseline metrics
        for detection in analysis.get("detections", []):
            self.baseline_metrics[detection["type"]] = detection.get("score", 0)
        
        logger.info(f"DriftDetector: baseline set from {len(events)} events")


def create_drift_detector(config: Optional[dict] = None) -> DriftDetector:
    """Create a configured drift detector."""
    return DriftDetector(config)
