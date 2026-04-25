import os
import logging
import json
from typing import Any

from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class DiscordConsumer(EventConsumer):
    """
    Consumer that sends Discord webhook alerts.
    
    Listens to:
    - bets_generated
    - run_finished
    
    Responsibilities:
    - Format Discord messages
    - Send webhook alerts
    - NO pipeline logic
    """

    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)

    def handles(self, event_type: str) -> bool:
        return event_type in ["bets_generated", "run_finished"]

    def process(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            logger.debug("Discord consumer disabled (no webhook URL)")
            return

        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == "bets_generated":
            self._handle_bets_generated(payload)
        elif event_type == "run_finished":
            self._handle_run_finished(payload)

    def _handle_bets_generated(self, payload: dict[str, Any]) -> None:
        """Handle bets_generated event - send top bets alert."""
        bets = payload.get("bets", [])
        if not bets:
            return

        run_id = payload.get("run_id", "unknown")
        
        # Format message
        embed = {
            "title": f" Value Bets Generated",
            "description": f"Found **{len(bets)}** value bets",
            "color": 3066993,
            "fields": [],
            "footer": {"text": f"Run: {run_id}"},
            "timestamp": payload.get("timestamp", "")
        }

        # Add top 5 bets as fields
        for i, bet in enumerate(bets[:5]):
            field = {
                "name": f"{bet.get('market', '?')} - {bet.get('outcome', '?')}",
                "value": f"EV: {bet.get('ev', 0):.2%} | Stake: {bet.get('stake', 0):.2f}",
                "inline": True
            }
            embed["fields"].append(field)

        if len(bets) > 5:
            embed["fields"].append({
                "name": "...",
                "value": f"+{len(bets) - 5} more bets",
                "inline": False
            })

        self._send_webhook(embed=embed)

    def _handle_run_finished(self, payload: dict[str, Any]) -> None:
        """Handle run_finished event - send completion summary."""
        run_id = payload.get("run_id", "unknown")
        mode = payload.get("mode", "unknown")
        total_bets = payload.get("total_bets", 0)
        total_ev = payload.get("total_ev", 0)
        errors = payload.get("errors", [])
        duration = payload.get("duration", 0)

        color = 3066993 if total_bets > 0 else 15158332
        
        embed = {
            "title": f" Pipeline Completed",
            "description": f"Mode: **{mode}** | Bets: **{total_bets}** | Total EV: **{total_ev:.2%}**",
            "color": color,
            "fields": [
                {"name": "Duration", "value": f"{duration:.1f}s", "inline": True},
                {"name": "Run ID", "value": run_id[:8] + "..." if len(run_id) > 8 else run_id, "inline": True},
            ],
            "footer": {"text": "Bootball"},
            "timestamp": payload.get("timestamp", "")
        }

        if errors:
            embed["fields"].append({
                "name": "Errors",
                "value": ", ".join(errors[:3]),
                "inline": False
            })
            embed["color"] = 15158332

        self._send_webhook(embed=embed)

    def _send_webhook(self, embed: dict) -> None:
        """Send embed to Discord webhook."""
        if not self.webhook_url:
            return

        try:
            import urllib.request
            import urllib.error
            
            data = json.dumps({"embeds": [embed]}).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 204:
                    logger.info("Discord webhook sent successfully")
                else:
                    logger.warning(f"Discord webhook returned {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send Discord webhook: {e}")