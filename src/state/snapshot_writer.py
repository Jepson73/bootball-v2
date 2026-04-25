"""
Snapshot Writer - saves state snapshots for fast reconstruction.

Rules:
- After every run_finished event: save snapshot
- Optionally: snapshot every N events
"""

import logging
from datetime import datetime
from typing import Optional

from src.state.snapshots import StateSnapshot
from src.state.snapshot_store import get_snapshot_store
from src.state.reconstructor import StateReconstructor
from src.events.event_store import get_event_store

logger = logging.getLogger(__name__)


class SnapshotWriter:
    """
    Writes state snapshots after events.
    
    Two triggers:
    1. After run_finished (is_complete=True)
    2. Every N events (configurable)
    """
    
    def __init__(self, event_threshold: int = 100):
        self.snapshot_store = get_snapshot_store()
        self.event_store = get_event_store()
        self.reconstructor = StateReconstructor()
        self.event_threshold = event_threshold  # Snapshot every N events
    
    def save_snapshot_from_events(
        self,
        run_id: Optional[str] = None,
        is_complete: bool = False
    ) -> Optional[StateSnapshot]:
        """
        Save snapshot of current state.
        
        Args:
            run_id: Associated run ID
            is_complete: True if run finished
            
        Returns:
            Saved snapshot or None
        """
        # Get current event count
        event_count = self.event_store.count()
        
        # Get latest event timestamp
        events = self.event_store.get_all_events(limit=1)
        last_timestamp = events[-1].get("timestamp", "") if events else None
        
        # Build state
        system = self.reconstructor.rebuild_from_events()
        
        # Serialize to dict
        snapshot = StateSnapshot(
            run_id=run_id,
            last_event_id=event_count,
            last_event_timestamp=last_timestamp,
            timestamp=datetime.utcnow(),
            betting_state={
                "balance": system.betting.balance,
                "roi": system.betting.roi,
                "pending_count": system.betting.pending_count,
                "wins": system.betting.wins,
                "losses": system.betting.losses,
                "pending_stake": system.betting.pending_stake,
                "total_pnl": system.betting.total_pnl,
                "bets": system.betting.bets,
                "rounds": system.betting.rounds,
            },
            health_state={
                "active_runs": system.health.active_runs,
                "completed_runs": system.health.completed_runs,
                "health_score": system.health.health_score,
                "error_rate": system.health.error_rate,
                "avg_duration": system.health.avg_duration,
                "total_runs": system.health.total_runs,
                "failed_runs": system.health.failed_runs,
            },
            model_state={
                "model_versions": system.model.model_versions,
                "market_performance": system.model.market_performance,
                "calibration_drift": system.model.calibration_drift,
                "roi_by_model": system.model.roi_by_model,
                "active_versions": system.model.active_versions,
                "retrain_signals": system.model.retrain_signals,
            },
            event_count=event_count,
            is_complete=is_complete,
        )
        
        # Save
        snapshot_id = self.snapshot_store.save_snapshot(snapshot)
        snapshot.id = snapshot_id
        
        logger.info(f"Saved snapshot {snapshot_id} (complete={is_complete})")
        return snapshot
    
    def should_snapshot(self) -> bool:
        """Check if we should save a periodic snapshot."""
        count = self.event_store.count()
        return count > 0 and count % self.event_threshold == 0


# Global instance
_snapshot_writer: Optional[SnapshotWriter] = None


def get_snapshot_writer() -> SnapshotWriter:
    """Get global snapshot writer."""
    global _snapshot_writer
    if _snapshot_writer is None:
        _snapshot_writer = SnapshotWriter()
    return _snapshot_writer


def save_run_snapshot(run_id: str, is_complete: bool = True) -> Optional[StateSnapshot]:
    """Convenience function to save snapshot after run."""
    writer = get_snapshot_writer()
    return writer.save_snapshot_from_events(run_id=run_id, is_complete=is_complete)
