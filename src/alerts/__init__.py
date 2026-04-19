"""
src/alerts/__init__.py

Alerts package for notifications.
"""
from src.alerts.discord import DiscordAlerts, discord_alerts, create_bet_alert, BetAlert

__all__ = ["DiscordAlerts", "discord_alerts", "create_bet_alert", "BetAlert"]
