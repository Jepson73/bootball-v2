import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from src.state.snapshots import StateSnapshot, SnapshotMetadata

logger = logging.getLogger(__name__)


class SnapshotStore:
    """
    JSONL-based snapshot storage.
    
    Stores snapshots for fast state reconstruction.
    """
    
    def __init__(self, path: str = "/opt/projects/bootball/data/snapshots.jsonl"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
    
    def save_snapshot(self, snapshot: StateSnapshot) -> int:
        """
        Save snapshot to store.
        
        Returns:
            Snapshot ID (line number)
        """
        data = {
            "id": snapshot.id,
            "run_id": snapshot.run_id,
            "last_event_id": snapshot.last_event_id,
            "last_event_timestamp": snapshot.last_event_timestamp,
            "timestamp": snapshot.timestamp.isoformat(),
            "betting_state": snapshot.betting_state,
            "health_state": snapshot.health_state,
            "model_state": snapshot.model_state,
            "event_count": snapshot.event_count,
            "is_complete": snapshot.is_complete,
            "version": snapshot.version,
        }
        
        with open(self.path, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")
        
        # Return line count as ID
        with open(self.path, "r") as f:
            line_count = sum(1 for line in f if line.strip())
        
        logger.info(f"Saved snapshot {line_count} for run {snapshot.run_id}")
        return line_count
    
    def get_latest_snapshot(self, run_id: Optional[str] = None) -> Optional[StateSnapshot]:
        """
        Get the latest snapshot.
        
        Args:
            run_id: Optional filter by run_id
            
        Returns:
            Latest StateSnapshot or None
        """
        if not os.path.exists(self.path):
            return None
        
        with open(self.path, "r") as f:
            lines = f.readlines()
        
        # Search backwards for latest matching run_id
        for line in reversed(lines):
            if not line.strip():
                continue
            
            try:
                data = json.loads(line)
                if run_id and data.get("run_id") != run_id:
                    continue
                
                return self._deserialize(data)
            except json.JSONDecodeError:
                continue
        
        return None
    
    def list_snapshots(
        self,
        run_id: Optional[str] = None,
        limit: int = 10
    ) -> list[SnapshotMetadata]:
        """
        List recent snapshots.
        
        Args:
            run_id: Optional filter
            limit: Max results
            
        Returns:
            List of metadata
        """
        if not os.path.exists(self.path):
            return []
        
        snapshots = []
        
        with open(self.path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    if run_id and data.get("run_id") != run_id:
                        continue
                    
                    meta = SnapshotMetadata(
                        snapshot_id=data.get("id", 0),
                        run_id=data.get("run_id"),
                        last_event_timestamp=data.get("last_event_timestamp"),
                        timestamp=data.get("timestamp", ""),
                        event_count=data.get("event_count", 0),
                        is_complete=data.get("is_complete", False),
                        betting_balance=data.get("betting_state", {}).get("balance", 0),
                        health_score=data.get("health_state", {}).get("health_score", 100),
                    )
                    snapshots.append(meta)
                except (json.JSONDecodeError, KeyError):
                    continue
        
        return snapshots[-limit:]
    
    def get_snapshot_by_id(self, snapshot_id: int) -> Optional[StateSnapshot]:
        """Get snapshot by ID."""
        if not os.path.exists(self.path):
            return None
        
        with open(self.path, "r") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                
                if i != snapshot_id:
                    continue
                
                try:
                    data = json.loads(line)
                    return self._deserialize(data)
                except json.JSONDecodeError:
                    continue
        
        return None
    
    def clear(self) -> None:
        """Clear all snapshots (for testing)."""
        if os.path.exists(self.path):
            os.remove(self.path)
        logger.info("SnapshotStore cleared")
    
    def _deserialize(self, data: dict) -> StateSnapshot:
        """Deserialize JSON to StateSnapshot."""
        return StateSnapshot(
            id=data.get("id"),
            run_id=data.get("run_id"),
            last_event_id=data.get("last_event_id", 0),
            last_event_timestamp=data.get("last_event_timestamp"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.utcnow(),
            betting_state=data.get("betting_state", {}),
            health_state=data.get("health_state", {}),
            model_state=data.get("model_state", {}),
            event_count=data.get("event_count", 0),
            is_complete=data.get("is_complete", False),
            version=data.get("version", "1.0"),
        )


# Global instance
_snapshot_store: Optional[SnapshotStore] = None


def get_snapshot_store() -> SnapshotStore:
    """Get global snapshot store."""
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = SnapshotStore()
    return _snapshot_store