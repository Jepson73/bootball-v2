"""
src/alerts/discord.py

Discord webhook alerts for betting opportunities.
Sends formatted messages with top N value bets.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

from config.settings import settings
from src.utils.timezone import format_local

logger = logging.getLogger(__name__)


@dataclass
class BetAlert:
    """A betting opportunity alert."""
    fixture_id: int
    home_team: str
    away_team: str
    league: str
    market: str
    market_display: str
    outcome: str
    odds: float
    our_prob: float
    ev: float
    confidence: int
    kickoff: str
    kickoff_local: str  # Local time string
    ev_stars: str  # Unicode stars for visual EV rating


class DiscordAlerts:
    """Sends alerts to Discord via webhook."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or settings.discord_webhook_url
        self.enabled = settings.alerts_enabled and bool(self.webhook_url)

    def send(self, content: str, embed: dict | None = None) -> bool:
        """Send a message to Discord.

        Args:
            content: Simple text message
            embed: Discord embed object (optional)

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            logger.debug("Discord alerts disabled")
            return False

        payload: dict[str, Any] = {"content": content}
        if embed:
            payload["embeds"] = [embed]

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if response.status_code == 204:
                logger.info("Discord alert sent successfully")
                return True
            else:
                logger.warning(f"Discord alert failed: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Discord alert error: {e}")
            return False

    def send_bet_alerts(self, bets: list[BetAlert]) -> bool:
        """Send top bets as a formatted Discord message.

        Args:
            bets: List of BetAlert objects (already top N)

        Returns:
            True if sent successfully
        """
        if not bets:
            logger.debug("No bets to send")
            return False

        n = len(bets)
        ev_stars_total = sum(b.ev_stars.count("⭐") for b in bets)

        header = (
            f"⚽ **Top {n} Value Bets** | {format_local(datetime.now(timezone.utc), '%b %d, %H:%M')} {tz_name()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        body_parts = []
        for i, bet in enumerate(bets, 1):
            body_parts.append(
                f"**{i}. {bet.home_team} vs {bet.away_team}**\n"
                f"   🏆 {bet.league} | ⏰ {bet.kickoff_local}\n"
                f"   📊 {bet.market_display}: **{bet.outcome}** @ {bet.odds:.2f}\n"
                f"   🎯 Our Prob: {bet.our_prob:.1%} | EV: {bet.ev:.1%} {bet.ev_stars}\n"
                f"   📈 Confidence: {bet.confidence}%"
            )

        body = "\n".join(body_parts)

        footer = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Total EV Stars: {ev_stars_total}⭐ | "
            f"🔬 Model-driven selections"
        )

        content = f"{header}\n{body}\n{footer}"

        embed = {
            "title": f"⚽ Top {n} Value Bets",
            "description": content,
            "color": 5815633,  # Green-ish
            "footer": {
                "text": f"Bootball AI | {format_local(datetime.now(timezone.utc), '%Y-%m-%d %H:%M')} {tz_name()}"
            },
        }

        return self.send("", embed=embed)


def create_bet_alert(
    fixture_id: int,
    home_team: str,
    away_team: str,
    league: str,
    market: str,
    outcome: str,
    odds: float,
    our_prob: float,
    ev: float,
    kickoff: datetime,
) -> BetAlert:
    """Create a BetAlert from raw data.

    Args:
        fixture_id: Fixture ID
        home_team: Home team name
        away_team: Away team name
        league: League name
        market: Market ID (btts, ou25, etc.)
        outcome: Selected outcome (Yes, Over, Home, etc.)
        odds: Decimal odds
        our_prob: Our probability (0-1)
        ev: Expected value as decimal (0.1 = 10%)
        kickoff: Match kickoff time

    Returns:
        Formatted BetAlert
    """
    market_displays = {
        "btts": "Both Teams To Score",
        "ou25": "Over/Under 2.5",
        "ou15": "Over/Under 1.5",
        "h2h": "Match Winner",
    }

    confidence = int(our_prob * 100)

    ev_stars = "⭐" * min(int(ev * 10), 5)
    if ev >= 0.3:
        ev_stars += "🔥"

    return BetAlert(
        fixture_id=fixture_id,
        home_team=home_team,
        away_team=away_team,
        league=league,
        market=market,
        market_display=market_displays.get(market, market.upper()),
        outcome=outcome,
        odds=odds,
        our_prob=our_prob,
        ev=ev,
        confidence=confidence,
        kickoff=kickoff.strftime("%H:%M %b %d") if kickoff else "TBD",
        kickoff_local=format_local(kickoff, "%H:%M %b %d") if kickoff else "TBD",
        ev_stars=ev_stars,
    )


def ev_to_stars(ev: float) -> str:
    """Convert EV to star rating.

    Args:
        ev: Expected value as decimal (0.1 = 10%)

    Returns:
        Star string like "⭐⭐⭐"
    """
    stars = min(int(ev * 10), 5)
    return "⭐" * stars if stars > 0 else ""


discord_alerts = DiscordAlerts()
