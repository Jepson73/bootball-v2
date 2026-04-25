"""
Monitoring Coordinator - ties together drift detection and EventBus.

Maintains continuous monitoring and emits alerts via EventBus.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from config.drift_thresholds import get_threshold_config
from src.events.event_store import get_event_store
from src.alerts.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


class MonitoringCoordinator:
    """
    Coordinates continuous monitoring of the event stream.
    
    Runs in background and emits drift/anomaly events to EventBus.
    """
    
    def __init__(self):
        self.config = get_threshold_config()
        
        # Import here to avoid circular dependencies
        from src.monitoring.drift_detector import create_drift_detector
        from src.monitoring.window_processor import get_window_processor
        
        self.drift_detector = create_drift_detector(self.config)
        self.window_processor = get_window_processor()
        
        # Alert cooldown tracking
        self.last_alert_time = {}
        self.cooldown_seconds = self.config.get("alert_cooldown_seconds", 300)
        
        # Running state
        self.is_running = False
        
        logger.info("MonitoringCoordinator initialized")
    
    def start(self, load_history: bool = True) -> None:
        """Start continuous monitoring."""
        if self.is_running:
            logger.warning("MonitoringCoordinator already running")
            return
        
        # Load recent events from store
        if load_history:
            hours = self.config.get("monitoring_time_window_hours", 24)
            self.window_processor.load_from_store(hours=hours)
            logger.info(f"Loaded {self.window_processor.get_window_stats()['event_count']} events")
        
        # Register callback for new events
        self.window_processor.register_detector(self._on_new_event)
        
        self.is_running = True
        logger.info("MonitoringCoordinator started")
    
    def stop(self) -> None:
        """Stop continuous monitoring."""
        self.is_running = False
        logger.info("MonitoringCoordinator stopped")
    
    def _on_new_event(self, event: dict, window: list[dict]) -> None:
        """
        Process new event through detector.
        
        Args:
            event: The new event
            window: Current event window
        """
        if not event:
            return
        
        # Only analyze on significant events
        event_type = event.get("event_type")
        if event_type not in ["bets_generated", "bet_settled", "run_finished", "predictions_generated"]:
            return
        
        # Run detection analysis
        results = self.drift_detector.analyze_event_window(window)
        
        # Check for detections and emit alerts
        for detection in results.get("detections", []):
            self._emit_alert_if_needed(detection)
    
    def _emit_alert_if_needed(self, detection: dict) -> None:
        """
        Emit alert to EventBus if not in cooldown.
        
        Args:
            detection: Detection result dict
        """
        detection_type = detection.get("type")
        severity = detection.get("severity", "none")
        
        if severity == "none":
            return
        
        # Check cooldown
        if not self._should_alert(detection_type):
            return
        
        # Map to EventBus event type
        if detection_type == "model_drift":
            event_type = Events.HEALTH_UPDATE
        elif detection_type == "market_shift":
            event_type = Events.HEALTH_UPDATE
        elif detection_type == "roi_anomaly":
            event_type = Events.HEALTH_UPDATE
        else:
            event_type = Events.HEALTH_UPDATE
        
        # Emit to EventBus
        event_bus.emit(event_type, {
            "detection_type": detection_type,
            "severity": severity,
            "score": detection.get("score", 0),
            "details": detection.get("details", {}),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Update last alert time
        self.last_alert_time[detection_type] = time.time()
        
        # Trigger retraining if high severity
        if severity == "high":
            self._trigger_retraining(detection)
    
    def _trigger_retraining(self, detection: dict) -> None:
        """
        Trigger model retraining based on detection.
        
        Args:
            detection: The detection that triggered retraining
        """
        detection_type = detection.get("type")
        
        # Map to market
        market_map = {
            "model_drift": "h2h",
            "market_shift": "btts", 
            "roi_anomaly": "h2h",
        }
        market = market_map.get(detection_type, "h2h")
        
        # Get lifecycle manager
        from src.models.lifecycle import get_lifecycle_manager
        lifecycle = get_lifecycle_manager()
        
        # Evaluate trigger
        trigger_result = lifecycle.evaluate_retrain_trigger(
            drift_report={"detections": [detection]},
            performance_report={}
        )
        
        if trigger_result.get("should_retrain"):
            # Queue retraining
            from src.models.retrain_worker import get_retrain_worker
            worker = get_retrain_worker()
            
            context = {
                "trigger": detection_type,
                "detection": detection,
                "reasons": trigger_result.get("reasons", []),
                "severity": trigger_result.get("severity"),
            }
            
            job_id = worker.queue_retrain(market, context)
            logger.info(f"Triggered retraining job {job_id} for market {market}")
        
        logger.warning(
            f"DRIFT ALERT: {detection_type} - {severity} "
            f"(score: {detection.get('score', 0):.2f})"
        )
    
    def _should_alert(self, detection_type: str) -> bool:
        """Check if alert should be emitted (cooldown check)."""
        last_time = self.last_alert_time.get(detection_type, 0)
        return (time.time() - last_time) >= self.cooldown_seconds
    
    def run_analysis(self, hours: int = 24) -> dict:
        """
        Run one-time analysis on recent events.
        
        Args:
            hours: Number of hours to analyze
            
        Returns:
            Analysis results
        """
        # Load events
        since = datetime.utcnow() - timedelta(hours=hours)
        events = self.event_store.get_events(since=since)
        
        # Run detection
        results = self.drift_detector.analyze_event_window(events)
        
        return results
    
    def get_status(self) -> dict:
        """Get monitoring status."""
        return {
            "is_running": self.is_running,
            "window_stats": self.window_processor.get_window_stats(),
            "config": self.config,
            "active_alerts": list(self.last_alert_time.keys())
        }


# Global coordinator instance
_coordinator: Optional[MonitoringCoordinator] = None


def get_monitoring_coordinator() -> MonitoringCoordinator:
    """Get global monitoring coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = MonitoringCoordinator()
    return _coordinator


def start_monitoring() -> None:
    """Start continuous monitoring."""
    coordinator = get_monitoring_coordinator()
    coordinator.start()


def run_monitoring_cycle() -> dict:
    """Run a single monitoring cycle."""
    coordinator = get_monitoring_coordinator()
    return coordinator.run_analysis()
