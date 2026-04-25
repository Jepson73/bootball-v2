from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class StateSnapshot:
    """
    Snapshot of system state at a point in time.
    
    Used for incremental reconstruction - we resume from
    the latest snapshot instead of replaying all events.
    """
    id: Optional[int] = None
    run_id: Optional[str] = None
    last_event_id: int = 0  # Position in event log
    last_event_timestamp: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Serialized state at this point
    betting_state: dict = field(default_factory=dict)
    health_state: dict = field(default_factory=dict)
    model_state: dict = field(default_factory=dict)
    
    # Metadata
    event_count: int = 0  # Total events at snapshot time
    is_complete: bool = False  # True if run finished
    version: str = "1.0"


@dataclass
class SnapshotMetadata:
    """Metadata about snapshot for display."""
    snapshot_id: int
    run_id: Optional[str]
    last_event_timestamp: Optional[str]
    timestamp: str
    event_count: int
    is_complete: bool
    betting_balance: float
    health_score: float