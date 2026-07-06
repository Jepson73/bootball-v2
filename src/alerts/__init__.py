"""
src/alerts/__init__.py

Alerts package for notifications.
"""
from src.events.event_bus import event_bus, Events

__all__ = ["event_bus", "Events"]
