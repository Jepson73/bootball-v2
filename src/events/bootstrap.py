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
from src.events.consumers.policy_consumer import PolicyConsumer
from src.events.consumers.calibration_consumer import CalibrationConsumer
from src.events.consumers.registry import registry

logger = logging.getLogger(__name__)


def bootstrap_consumers() -> None:
    """
    Initialize and register all event consumers.

    Call this once at application startup to wire up
    all consumers to the EventBus.
    """
    from src.events.consumers.clve_consumer import CLVEConsumer
    from config.settings import settings

    consumers = [
        BettingDashboardConsumer(),
        HealthDashboardConsumer(),
        ModelTrendConsumer(),
        ModelLifecycleConsumer(),
        # CalibrationConsumer stays registered unconditionally: its
        # CALIBRATION_DRIFT_DETECTED handler triggers real auto-recalibration
        # (a prediction-layer action, not a notification) — see Phase 28
        # Separation Principle. Its own Discord ping is gated internally
        # (_send_webhook checks settings.discord_v1_enabled) so the action
        # always runs even when V1's Discord voice is off.
        CalibrationConsumer(),
    ]

    # Phase 30 (Separation Principle): these three consumers do nothing but
    # send V1/betting-era Discord messages (per-market picks, POLICY ENGINE
    # REPORT, Adaptation Score / Closed Loop theater) — no dashboard/DB/model
    # responsibility. Default OFF; flip settings.discord_v1_enabled to
    # temporarily resurrect them for debugging.
    if settings.discord_v1_enabled:
        consumers += [
            DiscordConsumer(),
            PolicyConsumer(),
            CLVEConsumer(),
        ]
    else:
        logger.info("V1 Discord-only consumers (Discord/Policy/CLVE) not registered — discord_v1_enabled=False")

    for consumer in consumers:
        registry.register_consumer(consumer)

    logger.info(f"Bootstrapped {len(consumers)} consumers: {[c.name for c in consumers]}")


def shutdown_consumers() -> None:
    """Unregister all consumers (for testing/shutdown)."""
    for name in registry.list_consumers():
        registry.unregister_consumer(name)
    
    logger.info("All consumers unregistered")
