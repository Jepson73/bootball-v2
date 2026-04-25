import logging
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class ModelTrendConsumer(EventConsumer):
    """
    Consumer that tracks model performance trends.
    
    Listens to:
    - model_trend
    - run_finished
    
    Responsibilities:
    - Track ROI by model version
    - Track calibration drift
    - Track market performance (h2h, btts, ou25)
    """

    def __init__(self):
        self.trend_file = "/opt/projects/bootball/data/model_trends.json"
        self._load_state()

    def handles(self, event_type: str) -> bool:
        return event_type in ["model_trend", "run_finished"]

    def process(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == "model_trend":
            self._handle_model_trend(payload)
        elif event_type == "run_finished":
            self._handle_run_finished(payload)

    def _load_state(self) -> dict:
        """Load current state from file."""
        import os
        if os.path.exists(self.trend_file):
            try:
                import json
                with open(self.trend_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "market_performance": {},
            "model_versions": [],
            "calibration_drift": {},
            "last_updated": None
        }

    def _save_state(self, state: dict) -> None:
        """Save state to file."""
        import os
        import json
        os.makedirs(os.path.dirname(self.trend_file), exist_ok=True)
        with open(self.trend_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _handle_model_trend(self, payload: dict[str, Any]) -> None:
        """Handle model_trend event."""
        market = payload.get("market", "unknown")
        version = payload.get("model_version", "unknown")
        brier_score = payload.get("brier_score")
        ece = payload.get("ece")
        accuracy = payload.get("accuracy")
        
        state = self._load_state()
        
        # Update market performance
        if market not in state["market_performance"]:
            state["market_performance"][market] = []
            
        state["market_performance"][market].append({
            "version": version,
            "brier_score": brier_score,
            "ece": ece,
            "accuracy": accuracy,
            "timestamp": payload.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
        })
        
        # Keep only last 50 entries per market
        state["market_performance"][market] = state["market_performance"][market][-50:]
        
        # Track calibration drift
        if market not in state["calibration_drift"]:
            state["calibration_drift"][market] = []
            
        if ece is not None:
            state["calibration_drift"][market].append({
                "ece": ece,
                "timestamp": payload.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
            })
        
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)
        
        logger.info(f"ModelTrendConsumer: updated trend for {market} v{version}")

    def _handle_run_finished(self, payload: dict[str, Any]) -> None:
        """Handle run_finished event - update model performance from bets."""
        total_bets = payload.get("total_bets", 0)
        mode = payload.get("mode", "unknown")
        
        if total_bets == 0:
            return
            
        state = self._load_state()
        
        # Track completed runs count per mode
        if mode not in state["market_performance"]:
            state["market_performance"][mode] = []
            
        state["market_performance"][mode].append({
            "version": "latest",
            "bets": total_bets,
            "timestamp": payload.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
        })
        
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)