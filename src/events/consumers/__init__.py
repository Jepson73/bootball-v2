"""
Event consumers for Bootball system.
"""

from src.events.consumers.base import EventConsumer, BatchEventConsumer
from src.events.consumers.discord_consumer import DiscordConsumer
from src.events.consumers.betting_dashboard_consumer import BettingDashboardConsumer
from src.events.consumers.health_dashboard_consumer import HealthDashboardConsumer
from src.events.consumers.model_trend_consumer import ModelTrendConsumer

__all__ = [
    "EventConsumer",
    "BatchEventConsumer",
    "DiscordConsumer",
    "BettingDashboardConsumer",
    "HealthDashboardConsumer",
    "ModelTrendConsumer",
]
