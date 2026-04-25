"""
Event Store - persistent event log for replay and reconstruction.

Provides:
- append_event(event) - append to log file
- get_events(run_id, since, event_types, limit) - query events
- ordered replay capability
"""

import json
import os
import threading
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EventStore:
    """
    File-based event store for persistence and replay.
    
    Events are stored as JSON Lines (one JSON object per line)
    for efficient append and ordered replay.
    """
    
    def __init__(self, path: str = "/opt/projects/bootball/data/events.jsonl"):
        self.path = path
        self._lock = threading.Lock()
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
    
    def append_event(self, event: dict[str, Any]) -> None:
        """
        Append an event to the log.
        
        Args:
            event: Event dict with at minimum event_type and timestamp
        """
        with self._lock:
            # Ensure timestamp
            if "timestamp" not in event:
                event["timestamp"] = datetime.utcnow().isoformat()
            
            # Ensure event_type
            if "event_type" not in event:
                logger.warning(f"Event missing event_type: {event}")
                return
            
            # Append to file
            with open(self.path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
            
            logger.debug(f"EventStore: appended {event['event_type']}")
    
    def get_events(
        self,
        run_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
        limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """
        Get events with filters.
        
        Args:
            run_id: Filter by run_id
            since: Filter events after this timestamp
            until: Filter events before this timestamp
            event_types: Filter by event type(s)
            limit: Maximum events to return
            
        Returns:
            List of events in order
        """
        if not os.path.exists(self.path):
            return []
        
        events = []
        
        with open(self.path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Apply filters
                if run_id and event.get("run_id") != run_id:
                    continue
                
                if since:
                    event_time = event.get("timestamp", "")
                    if event_time and event_time < since.isoformat():
                        continue
                
                if until:
                    event_time = event.get("timestamp", "")
                    if event_time and event_time > until.isoformat():
                        continue
                
                if event_types and event.get("event_type") not in event_types:
                    continue
                
                events.append(event)
                
                if limit and len(events) >= limit:
                    break
        
        return events
    
    def get_all_events(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Get all events with optional limit."""
        return self.get_events(limit=limit)
    
    def clear(self) -> None:
        """Clear all events (for testing)."""
        with self._lock:
            if os.path.exists(self.path):
                os.remove(self.path)
            logger.info("EventStore: cleared")
    
    def count(self) -> int:
        """Count total events."""
        if not os.path.exists(self.path):
            return 0
        
        with open(self.path, "r") as f:
            return sum(1 for line in f if line.strip())


# Global event store instance
_event_store: Optional[EventStore] = None


def get_event_store() -> EventStore:
    """Get the global event store instance."""
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


def append_event(event: dict[str, Any]) -> None:
    """Convenience function to append to global store."""
    get_event_store().append_event(event)
