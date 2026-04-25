"""
Realtime event streaming for Bootball.

Provides:
- EventStream: In-memory event buffer with subscription
- WebSocket: Real-time push (requires Flask-SocketIO)
- Polling fallback: /api/events endpoint
"""

from src.realtime.event_stream import (
    EventStream,
    get_event_stream,
    subscribe_to_events,
    push_event,
)
from src.realtime.ws_server import (
    setup_realtime,
    setup_websocket_routes,
    create_polling_routes,
)

__all__ = [
    "EventStream",
    "get_event_stream", 
    "subscribe_to_events",
    "push_event",
    "setup_realtime",
    "setup_websocket_routes",
    "create_polling_routes",
]
