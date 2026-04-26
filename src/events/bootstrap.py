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


def bootstrap_system() -> None:
    """
    Full system bootstrap - consumers + decision engine + handlers.
    
    Call this at application startup.
    """
    # Bootstrap consumers
    bootstrap_consumers()
    
    # Setup alert handlers with suppression
    try:
        from src.alerts.handlers import setup_alert_handlers
        setup_alert_handlers()
        logger.info("Alert handlers initialized")
    except Exception as e:
        logger.warning(f"Could not setup alert handlers: {e}")
    
    # Start decision engine
    try:
        from src.decision_engine import start_decision_engine
        start_decision_engine()
        logger.info("Decision engine started")
    except Exception as e:
        logger.warning(f"Could not start decision engine: {e}")
    
    # Setup capital allocator handler
    try:
        from src.alerts.handlers import get_capital_allocator_handler
        get_capital_allocator_handler()
        logger.info("Capital allocator handler initialized")
    except Exception as e:
        logger.warning(f"Could not setup capital allocator: {e}")
    
    # Start execution engine
    try:
        from src.betting.execution_engine import get_execution_engine
        get_execution_engine()
        logger.info("Execution engine started")
    except Exception as e:
        logger.warning(f"Could not start execution engine: {e}")
    
    # Start performance tracker
    try:
        from src.performance.performance_tracker import get_performance_tracker
        get_performance_tracker()
        logger.info("Performance tracker started")
    except Exception as e:
        logger.warning(f"Could not start performance tracker: {e}")
    
    # Start adaptive allocator
    try:
        from src.portfolio.adaptive_allocator import get_adaptive_allocator
        allocator = get_adaptive_allocator()
        # Initial calculation
        allocator.recalculate()
        logger.info("Adaptive allocator started")
    except Exception as e:
        logger.warning(f"Could not start adaptive allocator: {e}")
    
    logger.info("System bootstrap complete")


def shutdown_consumers() -> None:
    """Unregister all consumers (for testing/shutdown)."""
    for name in registry.list_consumers():
        registry.unregister_consumer(name)
    
    logger.info("All consumers unregistered")
