"""
Bootstrap event consumers and wire to EventBus.

This module initializes all consumers and registers them
with the EventBus dispatch system.
"""

import logging

from src.events.consumers import (
    DiscordConsumer,
    BettingDashboardConsumer,
    HealthDashboardConsumer,
    ModelTrendConsumer,
    ModelLifecycleConsumer,
)
from src.events.consumers.registry import registry

logger = logging.getLogger(__name__)


def bootstrap_consumers() -> None:
    """
    Initialize and register all event consumers.
    
    Call this once at application startup to wire up
    all consumers to the EventBus.
    """
    consumers = [
        DiscordConsumer(),
        BettingDashboardConsumer(),
        HealthDashboardConsumer(),
        ModelTrendConsumer(),
        ModelLifecycleConsumer(),
    ]
    
    for consumer in consumers:
        registry.register_consumer(consumer)
    
    logger.info(f"Bootstrapped {len(consumers)} consumers: {[c.name for c in consumers]}")


def shutdown_consumers() -> None:
    """Unregister all consumers (for testing/shutdown)."""
    for name in registry.list_consumers():
        registry.unregister_consumer(name)
    
    logger.info("All consumers unregistered")
