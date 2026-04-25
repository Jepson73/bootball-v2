"""
Discord webhook alerts for betting + system observability.

Now supports:
- Bet alerts
- System events (runs, logs, actions)
- Health updates (with diff tracking)
- Model trend + calibration updates
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import settings
from src.utils.timezone import format_local, tz_name

logger = logging.getLogger(__name__)


# =========================================================
# Helpers (presentation only)
# =========================================================

def format_ev_stars(ev: float) -> str:
    return "⭐" * min(int(ev * 10), 5)


def clamp(value: Any, limit: int = 1024) -> str:
    return str(value)[:limit]


# =========================================================
# Bet model
# =========================================================

@dataclass
class BetAlert:
    fixture_id: int
    home_team: str
    away_team: str
    home_logo: str | None
    away_logo: str | None
    league: str
    league_flag: str | None
    market: str
    market_display: str
    outcome: str
    odds: float
    our_prob: float
    ev: float
    confidence: int
    kickoff_local: str
    ev_stars: str


# =========================================================
# Discord client
# =========================================================

class DiscordAlerts:
    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or settings.discord_webhook_url
        self.enabled = settings.alerts_enabled and bool(self.webhook_url)

    # -----------------------------
    # Core sender
    # -----------------------------

    def send(self, content: str = "", embed: dict | None = None) -> bool:
        if not self.enabled:
            return False

        payload: dict[str, Any] = {"content": content}

        if embed:
            payload["embeds"] = [embed]

        try:
            r = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if r.status_code == 204:
                return True

            logger.warning(f"Discord error {r.status_code}: {r.text}")
            return False

        except Exception as e:
            logger.error(f"Discord exception: {e}")
            return False

    # =========================================================
    # BET ALERTS
    # =========================================================

    def send_bet_alerts(self, bets: list[BetAlert]) -> bool:
        if not bets:
            return False

        now = datetime.now(timezone.utc)

        fields = []
        for i, bet in enumerate(bets, 1):
            fields.append({
                "name": f"{i}. {bet.home_team} vs {bet.away_team}",
                "value": (
                    f"🏆 {bet.league}\n"
                    f"📊 {bet.market_display}: {bet.outcome} @ {bet.odds:.2f}\n"
                    f"🎯 P: {bet.our_prob:.1%} | EV: {bet.ev:.1%} {bet.ev_stars} | Conf: {bet.confidence}%\n"
                    f"⏰ {bet.kickoff_local}"
                ),
                "inline": False
            })

        embed = {
            "title": f"⚽ Top {len(bets)} Value Bets",
            "color": 0x58B368,
            "fields": fields,
            "footer": {
                "text": f"Bootball AI | {format_local(now, '%Y-%m-%d %H:%M')} {tz_name()}"
            }
        }

        return self.send("", embed=embed)

    # =========================================================
    # SYSTEM EVENTS
    # =========================================================

    def send_system_event(self, event: dict[str, Any]) -> bool:
        fields = [
            {"name": k, "value": clamp(v), "inline": False}
            for k, v in event.items()
            if k != "event_type"
        ]

        embed = {
            "title": f"📡 {event.get('event_type', 'SYSTEM_EVENT')}",
            "color": 0x3498DB,
            "fields": fields,
            "footer": {"text": "System Event Stream"}
        }

        return self.send("", embed=embed)

    # =========================================================
    # HEALTH MONITORING
    # =========================================================

    def send_health_update(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None = None
    ) -> bool:

        changes = []

        if previous:
            for k in current:
                if k in previous and current[k] != previous[k]:
                    changes.append(f"{k}: {previous[k]} → {current[k]}")

        embed = {
            "title": "🩺 System Health",
            "color": 0xF1C40F,
            "fields": [
                {
                    "name": "Status",
                    "value": current.get("status", "unknown"),
                    "inline": False
                },
                {
                    "name": "Changes",
                    "value": "\n".join(changes) if changes else "No changes",
                    "inline": False
                }
            ]
        }

        return self.send("", embed=embed)

    # =========================================================
    # MODEL / TREND MONITORING
    # =========================================================

    def send_model_trend(self, trend: dict[str, Any]) -> bool:
        embed = {
            "title": "📊 Model Performance Update",
            "color": 0x9B59B6,
            "fields": [
                {"name": k, "value": clamp(v), "inline": True}
                for k, v in trend.items()
            ],
            "footer": {"text": "Calibration & Drift Tracking"}
        }

        return self.send("", embed=embed)


# =========================================================
# SINGLETON
# =========================================================

discord_alerts = DiscordAlerts()
