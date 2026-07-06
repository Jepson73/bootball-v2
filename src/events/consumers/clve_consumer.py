"""
CLVE Consumer - handles closed-loop validation events and Discord reporting.
"""

import os
import logging
from typing import Any

from src.events.consumers.base import EventConsumer
from src.events.event_bus import Events

logger = logging.getLogger(__name__)


class CLVEConsumer(EventConsumer):
    """
    Consumer that handles CLVE events and sends Discord reports.
    
    Listens to:
    - SYSTEM_ADAPTIVE_CONFIRMED
    - SYSTEM_STATIC_DETECTED
    - CLOSED_LOOP_VALIDATION_COMPLETED
    - ADAPTATION_SCORE_UPDATED
    
    Responsibilities:
    - Format validation messages
    - Send webhook alerts
    - NO pipeline logic
    """
    
    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)
        
        self.event_types = [
            Events.SYSTEM_ADAPTIVE_CONFIRMED,
            Events.SYSTEM_STATIC_DETECTED,
            Events.CLOSED_LOOP_VALIDATION_COMPLETED,
            Events.ADAPTATION_SCORE_UPDATED,
        ]
    
    def handles(self, event_type: str) -> bool:
        return event_type in self.event_types
    
    def process(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            logger.debug("CLVE consumer disabled (no webhook URL)")
            return
        
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        
        if event_type == Events.SYSTEM_ADAPTIVE_CONFIRMED:
            self._handle_adaptive_confirmed(payload)
        elif event_type == Events.SYSTEM_STATIC_DETECTED:
            self._handle_static_detected(payload)
        elif event_type == Events.CLOSED_LOOP_VALIDATION_COMPLETED:
            self._handle_validation_completed(payload)
        elif event_type == Events.ADAPTATION_SCORE_UPDATED:
            self._handle_score_updated(payload)
    
    def _handle_adaptive_confirmed(self, payload: dict[str, Any]) -> None:
        """Handle SYSTEM_ADAPTIVE_CONFIRMED event."""
        run_id = payload.get("run_id", "unknown")
        score = payload.get("adaptive_score", 0)
        pds = payload.get("pds", 0)
        ai = payload.get("ai", 0)
        
        message = {
            "title": "✅ System Adaptation Confirmed",
            "description": f"Run `{run_id}` - Closed-loop active",
            "color": 3066993,  # Green
            "fields": [
                {
                    "name": "Adaptation Score",
                    "value": f"{score:.2f}",
                    "inline": True,
                },
                {
                    "name": "Portfolio Drift",
                    "value": f"{pds:.4f}",
                    "inline": True,
                },
                {
                    "name": "Adaptation Index",
                    "value": f"{ai:.4f}",
                    "inline": True,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_static_detected(self, payload: dict[str, Any]) -> None:
        """Handle SYSTEM_STATIC_DETECTED event."""
        run_id = payload.get("run_id", "unknown")
        reason = payload.get("reason", "Unknown")
        details = payload.get("details", {})
        
        message = {
            "title": "⚠️ SYSTEM NOT ADAPTING",
            "description": f"Run `{run_id}` - Execution may be BLOCKED",
            "color": 15535002,  # Dark Red
            "fields": [
                {
                    "name": "Issue",
                    "value": reason,
                    "inline": False,
                },
                {
                    "name": "Details",
                    "value": f"PDS: {details.get('pds', 0):.4f}, AI: {details.get('ai', 0):.4f}, CDS: {details.get('cds', 0):.4f}",
                    "inline": False,
                },
                {
                    "name": "Action Required",
                    "value": "Check portfolio feedback wiring",
                    "inline": False,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
        logger.critical(f"[CLVE] SYSTEM NOT ADAPTING: {reason}")
    
    def _handle_validation_completed(self, payload: dict[str, Any]) -> None:
        """Handle CLOSED_LOOP_VALIDATION_COMPLETED event."""
        run_id = payload.get("run_id", "unknown")
        status = payload.get("status", "unknown")
        score = payload.get("adaptive_score", 0)
        
        message = {
            "title": "📊 CLOSED LOOP VALIDATION",
            "description": f"Run `{run_id}` validation complete",
            "color": 3066993 if status == "SELF_ADAPTING" else 15105570,
            "fields": [
                {
                    "name": "Status",
                    "value": status,
                    "inline": True,
                },
                {
                    "name": "Score",
                    "value": f"{score:.2f}",
                    "inline": True,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_score_updated(self, payload: dict[str, Any]) -> None:
        """Handle ADAPTATION_SCORE_UPDATED event."""
        score = payload.get("score", 0)
        
        message = {
            "title": "📈 Adaptation Score Updated",
            "description": f"New score: **{score:.2f}**",
            "color": 3066993,
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _send_webhook(self, message: dict) -> None:
        """Send Discord webhook."""
        import requests
        
        if not self.webhook_url:
            logger.warning("[CLVE] No Discord webhook URL configured")
            return
        
        try:
            response = requests.post(
                self.webhook_url,
                json={"embeds": [message]},
                timeout=10
            )
            response.raise_for_status()
            logger.info("[CLVE] Discord message sent successfully")
        except Exception as e:
            logger.error(f"[CLVE] Failed to send Discord message: {e}")
