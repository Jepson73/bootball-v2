"""
Drift Detection Thresholds Configuration.

Adjust these values without code changes.
"""

# ── Model Drift Detection ──────────────────────────────────────
# Alert when drift score exceeds this threshold (0-1)
DRIFT_ALERT_THRESHOLD = 0.15

# ── ROI Anomaly Detection ──────────────────────────────────────
# Alert when ROI drops more than this percentage
ROI_DROP_THRESHOLD = 5.0  # percent

# Alert when volatility exceeds this threshold
VOLATILITY_THRESHOLD = 2.0

# ── Market Shift Detection ─────────────────────────────────────
# Alert when market EV drops below this threshold
MARKET_SHIFT_SENSITIVITY = 0.20  # EV threshold

# ── Monitoring Windows ────────────────────────────────────────
# Number of events to keep in count window
MONITORING_MAX_EVENTS = 1000

# Time window for monitoring (hours)
MONITORING_TIME_WINDOW_HOURS = 24

# ── Alert Cooldown ───────────────────────────────────────────
# Minimum seconds between same-type alerts
ALERT_COOLDOWN_SECONDS = 300  # 5 minutes

# ── Severity Thresholds ──────────────────────────────────────
SEVERITY_HIGH = 0.8
SEVERITY_MEDIUM = 0.5
SEVERITY_LOW = 0.3


# Helper function to get threshold config
def get_threshold_config() -> dict:
    """Get current threshold configuration."""
    return {
        "drift_alert_threshold": DRIFT_ALERT_THRESHOLD,
        "roi_drop_threshold": ROI_DROP_THRESHOLD,
        "volatility_threshold": VOLATILITY_THRESHOLD,
        "market_shift_sensitivity": MARKET_SHIFT_SENSITIVITY,
        "monitoring_max_events": MONITORING_MAX_EVENTS,
        "monitoring_time_window_hours": MONITORING_TIME_WINDOW_HOURS,
        "alert_cooldown_seconds": ALERT_COOLDOWN_SECONDS,
        "severity_high": SEVERITY_HIGH,
        "severity_medium": SEVERITY_MEDIUM,
        "severity_low": SEVERITY_LOW,
    }
