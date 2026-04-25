"""
Monitoring module for drift detection and anomaly detection.

Provides:
- DriftDetector: Detect model performance drift
- WindowProcessor: Rolling event windows
- MonitoringCoordinator: Continuous monitoring + EventBus integration
"""

from src.monitoring.drift_detector import DriftDetector, create_drift_detector
from src.monitoring.window_processor import WindowProcessor, get_window_processor
from src.monitoring.monitoring_coordinator import (
    MonitoringCoordinator,
    get_monitoring_coordinator,
    start_monitoring,
    run_monitoring_cycle,
)

__all__ = [
    "DriftDetector",
    "create_drift_detector",
    "WindowProcessor", 
    "get_window_processor",
    "MonitoringCoordinator",
    "get_monitoring_coordinator",
    "start_monitoring",
    "run_monitoring_cycle",
]
