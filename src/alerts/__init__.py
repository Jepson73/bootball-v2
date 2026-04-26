"""
src/alerts/__init__.py

Alerts package for notifications.
"""
from src.alerts.event_bus import event_bus, Events
from src.alerts.handlers import setup_alert_handlers

__all__ = ["event_bus", "Events", "setup_alert_handlers"]
