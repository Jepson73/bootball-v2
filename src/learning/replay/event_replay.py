"""
Event Replay System - reconstructs past decision cycles.

Enables:
- Backtesting
- Debugging allocation errors
- Model drift detection
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReconstructedState:
    """Reconstructed state for a run."""
    run_id: str
    timestamp: str
    predictions: List[dict]
    risk_profile: dict
    portfolio: List[dict]
    execution_results: List[dict]
    performance: dict


class EventReplay:
    """
    Reconstructs past decision cycles from event log.
    """
    
    def __init__(self):
        self._replay_buffer: List[ReconstructedState] = []
        self._max_buffer_size = 100
        
    def record_run(
        self,
        run_id: str,
        predictions: List[dict],
        risk_profile: dict,
        portfolio: List[dict],
        execution_results: List[dict],
        performance: dict
    ) -> None:
        """Record a run for later replay."""
        state = ReconstructedState(
            run_id=run_id,
            timestamp=datetime.utcnow().isoformat(),
            predictions=predictions,
            risk_profile=risk_profile,
            portfolio=portfolio,
            execution_results=execution_results,
            performance=performance,
        )
        
        self._replay_buffer.append(state)
        
        if len(self._replay_buffer) > self._max_buffer_size:
            self._replay_buffer = self._replay_buffer[-self._max_buffer_size:]
        
        logger.info(f"[REPLAY] Recorded run {run_id}")
    
    def replay_run(self, run_id: str) -> Optional[ReconstructedState]:
        """Replay a specific run by ID."""
        for state in reversed(self._replay_buffer):
            if state.run_id == run_id:
                logger.info(f"[REPLAY] Replaying run {run_id}")
                return state
        
        logger.warning(f"[REPLAY] Run {run_id} not found")
        return None
    
    def replay_last(self) -> Optional[ReconstructedState]:
        """Replay the most recent run."""
        if self._replay_buffer:
            return self._replay_buffer[-1]
        return None
    
    def replay_window(self, runs: int = 10) -> List[ReconstructedState]:
        """Replay the last N runs."""
        return self._replay_buffer[-runs:]
    
    def analyze_drift(self, metric: str, window: int = 20) -> float:
        """Analyze drift in a metric over window."""
        if len(self._replay_buffer) < 2:
            return 0.0
        
        values = []
        for state in self._replay_buffer[-window:]:
            if state.performance and metric in state.performance:
                values.append(state.performance[metric])
        
        if len(values) < 2:
            return 0.0
        
        # Compute drift as change in mean
        first_half = values[:len(values)//2]
        second_half = values[len(values)//2:]
        
        first_mean = sum(first_half) / len(first_half)
        second_mean = sum(second_half) / len(second_half)
        
        return second_mean - first_mean
    
    def get_history_summary(self) -> dict:
        """Get summary of replay history."""
        if not self._replay_buffer:
            return {"runs": 0}
        
        total_roi = []
        total_bets = []
        
        for state in self._replay_buffer:
            if state.performance:
                total_roi.append(state.performance.get("overall_roi", 0))
                total_bets.append(state.performance.get("total_bets", 0))
        
        return {
            "runs": len(self._replay_buffer),
            "avg_roi": sum(total_roi) / len(total_roi) if total_roi else 0,
            "total_bets": sum(total_bets),
            "last_run": self._replay_buffer[-1].run_id if self._replay_buffer else None,
        }
    
    def clear(self) -> None:
        """Clear replay buffer."""
        self._replay_buffer.clear()
        logger.info("[REPLAY] Buffer cleared")


# Global replay
_replay: Optional[EventReplay] = None


def get_event_replay() -> EventReplay:
    """Get global event replay."""
    global _replay
    if _replay is None:
        _replay = EventReplay()
    return _replay
