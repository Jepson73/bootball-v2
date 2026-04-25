"""
Model State Builder.

Reconstructs model performance state from events.
No SQL logic - purely event-driven reconstruction.
"""

import logging
from typing import Optional

from src.state.models import ModelState
from src.state.reconstructor import rebuild_model_state

logger = logging.getLogger(__name__)


def build_model_state(
    events: Optional[list[dict]] = None,
    since: Optional[str] = None
) -> ModelState:
    """
    Build model state for tracking.
    
    Args:
        events: Pre-provided events (optional)
        since: ISO timestamp string to filter events after
        
    Returns:
        ModelState with reconstructed values
    """
    from datetime import datetime
    
    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
    
    return rebuild_model_state(events)


def get_market_performance(market: str) -> list[dict]:
    """Get performance history for a specific market."""
    state = rebuild_model_state()
    return state.market_performance.get(market, [])


def get_calibration_drift(market: str) -> list[dict]:
    """Get calibration drift history for a market."""
    state = rebuild_model_state()
    return state.calibration_drift.get(market, [])


def get_active_model_versions() -> list[str]:
    """Get currently active model versions."""
    state = rebuild_model_state()
    return state.active_versions


def get_retrain_signals() -> list[dict]:
    """Get model retraining signals."""
    state = rebuild_model_state()
    return state.retrain_signals
