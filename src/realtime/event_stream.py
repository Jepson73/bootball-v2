"""
Event Stream Service.

Provides real-time event streaming to connected clients.
Supports WebSocket and polling fallbacks.
"""

import json
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Optional, Any

logger = logging.getLogger(__name__)


class EventStream:
    """
    In-memory event stream with subscription support.
    
    Maintains a rolling buffer of recent events and
    notifies subscribers of new events.
    """
    
    def __init__(self, buffer_size: int = 1000):
        self._buffer: deque = deque(maxlen=buffer_size)
        self._subscribers: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._last_event_id = 0
    
    def subscribe(self, callback: Callable[[dict], None]) -> None:
        """
        Subscribe to event stream.
        
        Args:
            callback: Function to call with new events
        """
        with self._lock:
            self._subscribers.append(callback)
        logger.info(f"Subscriber added. Total: {len(self._subscribers)}")
    
    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        """Unsubscribe from event stream."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        logger.info(f"Subscriber removed. Total: {len(self._subscribers)}")
    
    def push_event(self, event: dict) -> int:
        """
        Push event to stream and notify subscribers.
        
        Args:
            event: Event dict
            
        Returns:
            Event ID
        """
        with self._lock:
            self._last_event_id += 1
            event_id = self._last_event_id
            
            # Add to buffer with ID
            event_with_id = {
                "id": event_id,
                "timestamp": event.get("timestamp", datetime.utcnow().isoformat()),
                "event_type": event.get("event_type"),
                "payload": event.get("payload", {}),
            }
            
            self._buffer.append(event_with_id)
            
            # Notify subscribers
            for callback in self._subscribers:
                try:
                    callback(event_with_id)
                except Exception as e:
                    logger.error(f"Subscriber callback failed: {e}")
        
        return event_id
    
    def get_events(
        self,
        since_id: Optional[int] = None,
        event_types: Optional[list[str]] = None,
        limit: int = 100
    ) -> list[dict]:
        """
        Get events since ID (for polling fallback).
        
        Args:
            since_id: Return events after this ID
            event_types: Filter by event types
            limit: Max events to return
            
        Returns:
            List of events
        """
        with self._lock:
            events = list(self._buffer)
        
        # Filter by ID
        if since_id:
            events = [e for e in events if e.get("id", 0) > since_id]
        
        # Filter by type
        if event_types:
            events = [e for e in events if e.get("event_type") in event_types]
        
        # Apply limit
        return events[-limit:]
    
    def get_recent(self, limit: int = 50) -> list[dict]:
        """Get N most recent events."""
        with self._lock:
            return list(self._buffer)[-limit:]
    
    def get_buffer_size(self) -> int:
        """Get current buffer size."""
        with self._lock:
            return len(self._buffer)
    
    def clear(self) -> None:
        """Clear buffer (for testing)."""
        with self._lock:
            self._buffer.clear()
            self._last_event_id = 0


# Global instance
_event_stream: Optional[EventStream] = None


def get_event_stream() -> EventStream:
    """Get global event stream."""
    global _event_stream
    if _event_stream is None:
        _event_stream = EventStream()
    return _event_stream


def subscribe_to_events(callback: Callable[[dict], None]) -> None:
    """Convenience: subscribe to global event stream."""
    get_event_stream().subscribe(callback)


def push_event(event: dict) -> int:
    """Convenience: push to global event stream."""
    return get_event_stream().push_event(event)
