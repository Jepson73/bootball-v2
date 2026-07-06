"""
Event Window Processor - maintain rolling event windows for monitoring.

Provides time-based and count-based windowing for continuous monitoring.
"""

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Callable

from src.events.event_store import get_event_store

logger = logging.getLogger(__name__)


class WindowProcessor:
    """
    Maintain rolling event windows for continuous monitoring.
    
    Supports:
    - Count-based windows (last N events)
    - Time-based windows (last 24h, 7d, etc.)
    - Continuous feeding to detectors
    """
    
    def __init__(
        self,
        max_events: int = 1000,
        time_window_hours: int = 24
    ):
        self.max_events = max_events
        self.time_window_hours = time_window_hours
        
        # Event buffers
        self.event_count_window = deque(maxlen=max_events)
        self.event_time_window = deque()
        
        # Time boundaries
        self.window_start = datetime.utcnow()
        
        # Detector callbacks
        self.detector_callbacks = []
        
        # Event store for persistence
        self.event_store = get_event_store()
    
    def add_event(self, event: dict) -> None:
        """
        Add an event to the window.
        
        Args:
            event: Event dict
        """
        timestamp = event.get("timestamp")
        
        # Add to count window
        self.event_count_window.append(event)
        
        # Add to time window
        if timestamp:
            try:
                event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                event_time = datetime.utcnow()
            
            self.event_time_window.append((event_time, event))
        
        # Clean old time-based events
        self._clean_time_window()
        
        # Notify detectors if callbacks registered
        for callback in self.detector_callbacks:
            try:
                callback(event, self.get_current_window())
            except Exception as e:
                logger.error(f"Detector callback error: {e}")
    
    def add_events_batch(self, events: list[dict]) -> None:
        """Add multiple events at once."""
        for event in events:
            self.add_event(event)
    
    def get_current_window(self) -> list[dict]:
        """
        Get current event window.
        
        Returns:
            List of events in the window
        """
        # Combine count and time windows
        events = list(self.event_count_window)
        
        # Filter time window to within bounds
        cutoff = datetime.utcnow() - timedelta(hours=self.time_window_hours)
        time_events = [
            e for ts, e in self.event_time_window
            if ts >= cutoff
        ]
        
        # Merge and deduplicate
        all_events = {id(e): e for e in events + time_events}
        
        return sorted(all_events.values(), key=lambda e: e.get("timestamp", ""))
    
    def get_count_window(self, limit: Optional[int] = None) -> list[dict]:
        """Get last N events by count."""
        events = list(self.event_count_window)
        if limit:
            events = events[-limit:]
        return events
    
    def get_time_window(self, hours: Optional[int] = None) -> list[dict]:
        """Get events from last N hours."""
        if hours is None:
            hours = self.time_window_hours
        
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [
            e for ts, e in self.event_time_window
            if ts >= cutoff
        ]
    
    def register_detector(self, callback: Callable[[dict, list[dict]], None]) -> None:
        """
        Register a callback to be called on each new event.
        
        Args:
            callback: Function(event, current_window) -> None
        """
        self.detector_callbacks.append(callback)
        logger.info(f"Registered detector callback")
    
    def load_from_store(self, hours: int = 24) -> None:
        """
        Load recent events from event store.
        
        Args:
            hours: Number of hours to load
        """
        since = datetime.utcnow() - timedelta(hours=hours)
        events = self.event_store.get_events(since=since)
        
        self.add_events_batch(events)
        logger.info(f"Loaded {len(events)} events from store")
    
    def _clean_time_window(self) -> None:
        """Remove events outside the time window."""
        cutoff = datetime.utcnow() - timedelta(hours=self.time_window_hours)
        
        while self.event_time_window and self.event_time_window[0][0] < cutoff:
            self.event_time_window.popleft()
    
    def get_window_stats(self) -> dict:
        """Get statistics about current window."""
        events = self.get_current_window()
        
        return {
            "event_count": len(events),
            "time_window_hours": self.time_window_hours,
            "max_events": self.max_events,
            "oldest_event": events[0].get("timestamp") if events else None,
            "newest_event": events[-1].get("timestamp") if events else None,
            "event_types": list(set(e.get("event_type", "unknown") for e in events))
        }
    
    def clear(self) -> None:
        """Clear all windows."""
        self.event_count_window.clear()
        self.event_time_window.clear()
        logger.info("WindowProcessor cleared")


# Global instance
_window_processor: Optional[WindowProcessor] = None


def get_window_processor() -> WindowProcessor:
    """Get global window processor."""
    global _window_processor
    if _window_processor is None:
        _window_processor = WindowProcessor()
    return _window_processor
