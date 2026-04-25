"""
Health State Builder.

Reconstructs health dashboard state from events.
No SQL logic - purely event-driven reconstruction.
"""

import logging
from typing import Optional

from src.state.models import HealthState
from src.state.reconstructor import rebuild_health_state

logger = logging.getLogger(__name__)


def build_health_state(
    events: Optional[list[dict]] = None,
    since: Optional[str] = None
) -> HealthState:
    """
    Build health state for dashboard.
    
    Args:
        events: Pre-provided events (optional)
        since: ISO timestamp string to filter events after
        
    Returns:
        HealthState with reconstructed values
    """
    from datetime import datetime
    
    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
    
    return rebuild_health_state(events)


def get_system_health_score() -> float:
    """Get system health score from events."""
    state = rebuild_health_state()
    return state.health_score


def get_error_rate() -> float:
    """Get current error rate from events."""
    state = rebuild_health_state()
    return state.error_rate


def get_active_runs() -> list[dict]:
    """Get currently active runs from events."""
    state = rebuild_health_state()
    return state.active_runs


def get_run_history(limit: int = 50) -> list[dict]:
    """Get run history from events."""
    state = rebuild_health_state()
    return state.completed_runs[-limit:]
