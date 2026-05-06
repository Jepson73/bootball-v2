"""
Policy Consumer - handles policy engine events and Discord reporting.
"""

import os
import logging
from typing import Any

from src.events.consumers.base import EventConsumer
from src.alerts.event_bus import Events

logger = logging.getLogger(__name__)


class PolicyConsumer(EventConsumer):
    """
    Consumer that handles policy engine events and sends Discord reports.
    
    Listens to:
    - POLICY_APPROVED
    - POLICY_THROTTLED
    - POLICY_REJECTED
    - RISK_LIMIT_BREACHED
    - KILL_SWITCH_TRIGGERED
    
    Responsibilities:
    - Format policy decision messages
    - Send webhook alerts
    - NO pipeline logic
    """
    
    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)
        
        self.event_types = [
            Events.POLICY_APPROVED,
            Events.POLICY_THROTTLED,
            Events.POLICY_REJECTED,
            Events.RISK_LIMIT_BREACHED,
            Events.KILL_SWITCH_TRIGGERED,
        ]
    
    def handles(self, event_type: str) -> bool:
        return event_type in self.event_types
    
    def process(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            logger.debug("Policy consumer disabled (no webhook URL)")
            return
        
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        
        if event_type == Events.POLICY_APPROVED:
            self._handle_policy_approved(payload)
        elif event_type == Events.POLICY_THROTTLED:
            self._handle_policy_throttled(payload)
        elif event_type == Events.POLICY_REJECTED:
            self._handle_policy_rejected(payload)
        elif event_type == Events.RISK_LIMIT_BREACHED:
            self._handle_risk_limit_breached(payload)
        elif event_type == Events.KILL_SWITCH_TRIGGERED:
            self._handle_kill_switch_triggered(payload)
    
    def _handle_policy_approved(self, payload: dict[str, Any]) -> None:
        """Handle POLICY_APPROVED event."""
        risk_score = payload.get("risk_score", 0)
        
        message = {
            "title": "🛡 POLICY ENGINE REPORT",
            "description": "**Status: APPROVED**",
            "color": 3066993,  # Green
            "fields": [
                {
                    "name": "Risk Score",
                    "value": f"{risk_score:.2%}",
                    "inline": True,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_policy_throttled(self, payload: dict[str, Any]) -> None:
        """Handle POLICY_THROTTLED event."""
        risk_score = payload.get("risk_score", 0)
        violations = payload.get("violated_constraints", [])
        scale = payload.get("scale", 1.0)
        
        message = {
            "title": "🛡 POLICY ENGINE REPORT",
            "description": "**Status: THROTTLED**",
            "color": 15105570,  # Orange
            "fields": [
                {
                    "name": "Risk Score",
                    "value": f"{risk_score:.2%}",
                    "inline": True,
                },
                {
                    "name": "Violations",
                    "value": "\n".join([f"- {v}" for v in violations]) if violations else "None",
                    "inline": False,
                },
                {
                    "name": "Action",
                    "value": f"Allocation scaled to **{scale:.2f}x**",
                    "inline": False,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_policy_rejected(self, payload: dict[str, Any]) -> None:
        """Handle POLICY_REJECTED event."""
        risk_score = payload.get("risk_score", 0)
        violations = payload.get("violated_constraints", [])
        reason = payload.get("reject_reason", "Unknown")
        
        message = {
            "title": "🛡 POLICY ENGINE REPORT",
            "description": "**Status: REJECTED**",
            "color": 15132390,  # Red
            "fields": [
                {
                    "name": "Risk Score",
                    "value": f"{risk_score:.2%}",
                    "inline": True,
                },
                {
                    "name": "Violations",
                    "value": "\n".join([f"- {v}" for v in violations]) if violations else "None",
                    "inline": False,
                },
                {
                    "name": "Reason",
                    "value": reason,
                    "inline": False,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_risk_limit_breached(self, payload: dict[str, Any]) -> None:
        """Handle RISK_LIMIT_BREACHED event."""
        violations = payload.get("violated_constraints", [])
        risk_score = payload.get("risk_score", 0)
        
        message = {
            "title": "⚠️ RISK LIMIT BREACHED",
            "description": "One or more risk limits have been breached",
            "color": 15105570,  # Orange
            "fields": [
                {
                    "name": "Violated Constraints",
                    "value": "\n".join([f"- {v}" for v in violations]) if violations else "None",
                    "inline": False,
                },
                {
                    "name": "Risk Score",
                    "value": f"{risk_score:.2%}",
                    "inline": True,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
    
    def _handle_kill_switch_triggered(self, payload: dict[str, Any]) -> None:
        """Handle KILL_SWITCH_TRIGGERED event."""
        reason = payload.get("reject_reason", "Unknown")
        
        message = {
            "title": "🔥 KILL SWITCH TRIGGERED",
            "description": "**SYSTEM HALTED** - All betting disabled",
            "color": 15535002,  # Dark Red
            "fields": [
                {
                    "name": "Trigger Reason",
                    "value": reason,
                    "inline": False,
                },
            ],
            "timestamp": payload.get("timestamp", ""),
        }
        
        self._send_webhook(message)
        logger.critical(f"[POLICY] KILL SWITCH TRIGGERED: {reason}")
    
    def _send_webhook(self, message: dict) -> None:
        """Send Discord webhook."""
        import requests
        
        if not self.webhook_url:
            logger.warning("[POLICY] No Discord webhook URL configured")
            return
        
        try:
            response = requests.post(
                self.webhook_url,
                json={"embeds": [message]},
                timeout=10
            )
            response.raise_for_status()
            logger.info("[POLICY] Discord message sent successfully")
        except Exception as e:
            logger.error(f"[POLICY] Failed to send Discord message: {e}")
