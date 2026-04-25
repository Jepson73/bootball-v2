import logging
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class HealthDashboardConsumer(EventConsumer):
    """
    Consumer that computes and tracks system health.
    
    Listens to:
    - run_finished
    - health_update
    
    Responsibilities:
    - Compute system health score
    - Track pipeline latency
    - Track error rates
    - Track active runs
    """

    def __init__(self):
        self.health_file = "/opt/projects/bootball/data/health_state.json"
        self._load_state()

    def handles(self, event_type: str) -> bool:
        return event_type in ["run_finished", "run_started", "health_update"]

    def process(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == "run_started":
            self._handle_run_started(payload)
        elif event_type == "run_finished":
            self._handle_run_finished(payload)
        elif event_type == "health_update":
            self._handle_health_update(payload)

    def _load_state(self) -> dict:
        """Load current state from file."""
        import os
        if os.path.exists(self.health_file):
            try:
                import json
                with open(self.health_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "active_runs": [],
            "completed_runs": [],
            "health_score": 100,
            "error_rate": 0,
            "avg_duration": 0,
            "last_updated": None
        }

    def _save_state(self, state: dict) -> None:
        """Save state to file."""
        import os
        import json
        os.makedirs(os.path.dirname(self.health_file), exist_ok=True)
        with open(self.health_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _handle_run_started(self, payload: dict[str, Any]) -> None:
        """Handle run_started event."""
        run_id = payload.get("run_id")
        mode = payload.get("mode", "unknown")
        
        state = self._load_state()
        
        state["active_runs"].append({
            "run_id": run_id,
            "mode": mode,
            "started_at": payload.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
        })
        
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)
        
        logger.info(f"HealthDashboardConsumer: run started {run_id}")

    def _handle_run_finished(self, payload: dict[str, Any]) -> None:
        """Handle run_finished event - update health metrics."""
        run_id = payload.get("run_id")
        mode = payload.get("mode", "unknown")
        duration = payload.get("duration", 0)
        errors = payload.get("errors", [])
        
        state = self._load_state()
        
        # Remove from active runs
        state["active_runs"] = [
            r for r in state["active_runs"] 
            if r.get("run_id") != run_id
        ]
        
        # Add to completed runs
        completed = {
            "run_id": run_id,
            "mode": mode,
            "duration": duration,
            "errors": len(errors),
            "finished_at": payload.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
        }
        state["completed_runs"].append(completed)
        
        # Keep only last 100 completed runs
        state["completed_runs"] = state["completed_runs"][-100:]
        
        # Calculate health metrics
        total_runs = len(state["completed_runs"])
        if total_runs > 0:
            error_count = sum(r.get("errors", 0) for r in state["completed_runs"])
            state["error_rate"] = error_count / total_runs
            
            total_duration = sum(r.get("duration", 0) for r in state["completed_runs"])
            state["avg_duration"] = total_duration / total_runs
            
            # Health score: 100 - (error_rate * 50)
            state["health_score"] = max(0, 100 - (state["error_rate"] * 50))
        
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)
        
        logger.info(f"HealthDashboardConsumer: run finished {run_id}, errors={len(errors)}, duration={duration:.1f}s")

    def _handle_health_update(self, payload: dict[str, Any]) -> None:
        """Handle health_update event."""
        state = self._load_state()
        
        if "health_score" in payload:
            state["health_score"] = payload["health_score"]
        if "error_rate" in payload:
            state["error_rate"] = payload["error_rate"]
            
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)