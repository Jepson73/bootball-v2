"""
src/betting/alerts.py - Alert system for betting notifications

Supports:
- Discord webhook
- Telegram bot
- Slack webhook
- Console logging (for testing)

Setup (in .env):
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    TELEGRAM_BOT_TOKEN=xxx
    TELEGRAM_CHAT_ID=xxx
    SLACK_WEBHOOK_URL=https://hooks.slack.com/...

Usage:
    from src.betting.alerts import BettingAlerts, send_bet_alert

    alerts = BettingAlerts()
    alerts.send_bet_alert(
        market="btts",
        home_team="Arsenal",
        away_team="Chelsea",
        outcome="Yes",
        odds=2.10,
        ev=12.5,
        kelly=0.08,
    )
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class BetAlert:
    market: str
    home_team: str
    away_team: str
    outcome: str
    odds: float
    ev: float
    kelly: float
    league: Optional[str] = None
    edge: Optional[float] = None
    fixture_date: Optional[str] = None
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None


class AlertChannel:
    """Base class for alert channels."""

    def send(self, message: str) -> bool:
        raise NotImplementedError


class DiscordChannel(AlertChannel):
    """Discord webhook notifications."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            logger.warning("Discord not configured (missing DISCORD_WEBHOOK_URL)")
            return False

        try:
            response = requests.post(self.webhook_url, json={
                "content": message,
            }, timeout=10)
            return response.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Discord send failed: {e}")
            return False


class TelegramChannel(AlertChannel):
    """Telegram bot notifications."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    def send(self, message: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured (missing BOT_TOKEN or CHAT_ID)")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False


class SlackChannel(AlertChannel):
    """Slack webhook notifications."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            logger.warning("Slack not configured (missing WEBHOOK_URL)")
            return False

        try:
            response = requests.post(self.webhook_url, json={
                "text": message,
            }, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Slack send failed: {e}")
            return False


class ConsoleChannel(AlertChannel):
    """Console logging (for testing)."""

    def send(self, message: str) -> bool:
        logger.info(f"ALERT: {message}")
        return True


class BettingAlerts:
    """
    Multi-channel alert system for betting notifications.

    Usage:
        alerts = BettingAlerts(channels=["telegram", "slack"])
        alerts.send_bet_alert(...)
    """

    def __init__(
        self,
        channels: list[str] | None = None,
        min_ev: float = 10.0,
        min_odds: float = 1.5,
        min_kelly: float = 0.05,
    ):
        """
        Initialize alerts.

        Args:
            channels: List of channels to use ["discord", "telegram", "slack", "console"]
            min_ev: Minimum EV% to trigger alert
            min_odds: Minimum odds to trigger alert
            min_kelly: Minimum Kelly fraction to trigger alert
        """
        self.channels = channels or ["console"]
        self.min_ev = min_ev
        self.min_odds = min_odds
        self.min_kelly = min_kelly

        self._channels: list[AlertChannel] = []
        for ch in self.channels:
            if ch == "discord":
                self._channels.append(DiscordChannel())
            elif ch == "telegram":
                self._channels.append(TelegramChannel())
            elif ch == "slack":
                self._channels.append(SlackChannel())
            elif ch == "console":
                self._channels.append(ConsoleChannel())

    def send_message(self, message: str) -> None:
        """Send a raw message to all configured channels."""
        for channel in self._channels:
            try:
                channel.send(message)
            except Exception as e:
                logger.error(f"Failed to send alert via {type(channel).__name__}: {e}")

    def send_bet_alert(self, alert: BetAlert) -> bool:
        """
        Send a bet alert if it meets threshold criteria.

        Returns:
            True if alert was sent, False if filtered out
        """
        if alert.ev < self.min_ev:
            return False
        if alert.odds < self.min_odds:
            return False
        if alert.kelly < self.min_kelly:
            return False

        # Send to each channel with appropriate format
        for channel in self._channels:
            try:
                if isinstance(channel, DiscordChannel):
                    message = self._format_bet_alert_discord(alert)
                else:
                    message = self._format_bet_alert_html(alert)
                channel.send(message)
            except Exception as e:
                logger.error(f"Failed to send alert via {type(channel).__name__}: {e}")

        return True

    def _format_bet_alert(self, alert: BetAlert, for_discord: bool = False) -> str:
        """Format bet alert message."""
        if for_discord:
            return self._format_bet_alert_discord(alert)
        return self._format_bet_alert_html(alert)

    def _format_bet_alert_discord(self, alert: BetAlert) -> str:
        """Format bet alert message for Discord (markdown)."""
        market_emoji = {"btts": "🎯", "ou25": "⚽", "ou15": "🥅", "h2h": "🏆"}.get(alert.market, "💰")
        lines = [
            f"{market_emoji} **VALUE BET**",
            "",
            f"**{alert.home_team}** vs **{alert.away_team}**",
            f"Market: **{alert.market.upper()}**",
        ]

        if alert.fixture_date:
            lines.append(f"🕐 {alert.fixture_date}")

        if alert.league:
            lines.append(f"🏆 {alert.league}")

        lines.extend([
            "",
            f"Pick: **{alert.outcome}** | Odds: `{alert.odds:.2f}` | EV: **+{alert.ev:.1f}%**",
            f"Kelly: `{alert.kelly:.1%}`" + (f" | Edge: **+{alert.edge:.0f}%**" if alert.edge else ""),
            "",
            "──────────────────",
        ])

        return "\n".join(lines)

    def _format_bet_alert_html(self, alert: BetAlert) -> str:
        """Format bet alert message for HTML (Telegram)."""
        market_emoji = {"btts": "🎯", "ou25": "⚽", "ou15": "🥅", "h2h": "🏆"}.get(alert.market, "💰")
        lines = [
            f"{market_emoji} <b>VALUE BET ALERT</b>",
            "",
            f"<b>{alert.home_team}</b> vs <b>{alert.away_team}</b>",
            f"Market: <b>{alert.market.upper()}</b>",
            f"Pick: <b>{alert.outcome}</b>",
            f"Odds: <code>{alert.odds:.2f}</code>",
            f"EV: <b>+{alert.ev:.1f}%</b>",
            f"Kelly: {alert.kelly:.1%}",
        ]

        if alert.edge is not None:
            lines.append(f"Edge: <b>+{alert.edge:.0f}%</b>")

        if alert.league:
            lines.append(f"League: {alert.league}")

        lines.extend([
            "",
            "──────────────────",
        ])

        return "\n".join(lines)

    def send_bankroll_alert(self, balance: float, change: float, change_pct: float) -> None:
        """Send bankroll update alert."""
        emoji = "📈" if change >= 0 else "📉"
        message = (
            f"{emoji} <b>BANKROLL UPDATE</b>\n\n"
            f"Balance: <b>${balance:.2f}</b>\n"
            f"Change: <b>{change:+.2f}</b> ({change_pct:+.1f}%)"
        )
        self.send(message)

    def send_model_alert(self, roi: float, win_rate: float, reason: str) -> None:
        """Send model health alert."""
        emoji = "⚠️" if roi < 0 else "✅"
        message = (
            f"{emoji} <b>MODEL HEALTH ALERT</b>\n\n"
            f"Recent ROI: <b>{roi:+.1f}%</b>\n"
            f"Win Rate: <b>{win_rate:.1%}</b>\n"
            f"Reason: {reason}"
        )
        self.send(message)


def send_bet_alert(
    market: str,
    home_team: str,
    away_team: str,
    outcome: str,
    odds: float,
    ev: float,
    kelly: float,
    league: str | None = None,
    edge: float | None = None,
) -> bool:
    """
    Convenience function to send a bet alert.

    Returns:
        True if alert was sent, False if filtered
    """
    alerts = BettingAlerts()
    return alerts.send_bet_alert(BetAlert(
        market=market,
        home_team=home_team,
        away_team=away_team,
        outcome=outcome,
        odds=odds,
        ev=ev,
        kelly=kelly,
        league=league,
        edge=edge,
    ))


def send_data_alert(
    title: str,
    message: str,
    severity: str = "warning",
) -> None:
    """Send a data/system issue alert.
    
    Args:
        title: Alert title
        message: Alert message details
        severity: "info", "warning", "error"
    """
    emoji = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
    }.get(severity, "ℹ️")
    
    alerts = BettingAlerts(channels=["discord", "console"])
    alerts.send_message(
        f"{emoji} <b>DATA ALERT: {title}</b>\n\n"
        f"{message}"
    )


def send_bet_placed_alert(
    market: str,
    home_team: str,
    away_team: str,
    outcome: str,
    odds: float,
    stake: float,
    league: str | None = None,
) -> None:
    """Send alert when betting bot places a bet.
    
    Args:
        market: Betting market (btts, ou25, h2h, etc.)
        home_team: Home team name
        away_team: Away team name
        outcome: Selected outcome
        odds: Bet odds
        stake: Kelly stake amount
        league: Optional league name
    """
    alerts = BettingAlerts(channels=["discord", "console"])
    alerts.send_message(
        f"🎰 <b>BET PLACED</b>\n\n"
        f"<b>{home_team}</b> vs <b>{away_team}</b>\n"
        f"Market: {market.upper()}\n"
        f"Pick: <b>{outcome}</b> @ {odds:.2f}\n"
        f"Stake: <b>${stake:.2f}</b>\n"
        + (f"League: {league}" if league else "")
    )


def send_daily_run_alert(
    matches_analyzed: int,
    bets_placed: int,
    total_staked: float,
    status: str = "success",
    error: str | None = None,
) -> None:
    """Send alert when daily run completes.

    Args:
        matches_analyzed: Number of matches analyzed
        bets_placed: Number of bets placed
        total_staked: Total amount staked
        status: "success" or "error"
        error: Optional error message
    """
    emoji = "💻" if status == "success" else "❌"

    alerts = BettingAlerts(channels=["discord", "console"])

    if status == "success":
        alerts.send_message(
            f"{emoji} <b>DAILY RUN COMPLETE</b>\n\n"
            f"Matches analyzed: <b>{matches_analyzed}</b>\n"
            f"Bets placed: <b>{bets_placed}</b>\n"
            f"Total staked: <b>${total_staked:.2f}</b>"
        )
    else:
        alerts.send_message(
            f"{emoji} <b>DAILY RUN FAILED</b>\n\n"
            f"Error: {error}"
        )
