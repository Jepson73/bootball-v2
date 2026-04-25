"""
WebSocket Server for real-time event streaming.

Provides:
- WebSocket endpoint /ws/events
- Polling fallback endpoint /api/events
- Event subscription via EventStream

Uses Flask-SocketIO if available, otherwise falls back to SSE.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def setup_websocket_routes(app, event_stream):
    """
    Set up WebSocket routes with Flask app.
    
    Args:
        app: Flask application
        event_stream: EventStream instance
    """
    
    # Try to use Flask-SocketIO if available
    try:
        from flask_socketio import SocketIO, emit, disconnect
        
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
        
        @socketio.on("connect")
        def handle_connect():
            logger.info("WebSocket client connected")
            # Send recent events on connect
            recent = event_stream.get_recent(limit=20)
            for event in recent:
                emit("event", event)
        
        @socketio.on("disconnect")
        def handle_disconnect():
            logger.info("WebSocket client disconnected")
        
        @socketio.on("subscribe")
        def handle_subscribe(data):
            """Subscribe to specific event types."""
            event_types = data.get("event_types", [])
            logger.info(f"Client subscribed to: {event_types}")
        
        # Wire EventStream to push to SocketIO
        def ws_broadcast(event):
            socketio.emit("event", event)
        
        event_stream.subscribe(ws_broadcast)
        
        # Add socketio to app for running
        app.socketio = socketio
        
        logger.info("WebSocket server initialized with SocketIO")
        return socketio
        
    except ImportError:
        logger.warning("Flask-SocketIO not available, using polling fallback")
        
        # Polling fallback - register event stream as subscriber
        def polling_broadcast(event):
            # With polling, events go to the buffer, clients poll /api/events
            pass
        
        # Just subscribe to log
        event_stream.subscribe(polling_broadcast)
        
        return None


def create_polling_routes(app, event_stream):
    """
    Create polling fallback routes.
    
    Endpoints:
    - GET /api/events - poll for new events
    - GET /api/events/recent - get recent events
    """
    from flask import jsonify, request
    
    @app.route("/api/events")
    def get_events():
        """
        Polling endpoint for events.
        
        Query params:
            since_id: Return events after this ID
            event_types: Comma-separated event types
            limit: Max events (default 100)
            
        Returns:
            JSON list of events
        """
        since_id = request.args.get("since_id", type=int)
        event_types = request.args.get("event_types")
        limit = request.args.get("limit", default=100, type=int)
        
        if event_types:
            event_types = event_types.split(",")
        
        events = event_stream.get_events(
            since_id=since_id,
            event_types=event_types,
            limit=limit
        )
        
        return jsonify({
            "ok": True,
            "events": events,
            "server_time": event_stream.get_recent(1)[0].get("timestamp") if event_stream.get_recent(1) else None
        })
    
    @app.route("/api/events/recent")
    def get_recent_events():
        """Get recent events."""
        limit = request.args.get("limit", default=50, type=int)
        events = event_stream.get_recent(limit=limit)
        
        return jsonify({
            "ok": True,
            "events": events
        })
    
    logger.info("Polling fallback routes registered")


def setup_realtime(app, event_stream):
    """
    Set up complete realtime layer.
    
    Args:
        app: Flask application
        event_stream: EventStream instance
    """
    # Try WebSocket first
    socketio = setup_websocket_routes(app, event_stream)
    
    # Always add polling fallback
    create_polling_routes(app, event_stream)
    
    return socketio
