"""
Action definitions for Decision Engine.

Defines structured actions that the Decision Engine can emit.
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Action:
    type: str
    payload: dict


# Action type constants
RETRAIN_MODEL = "RETRAIN_MODEL"
DISABLE_MARKET = "DISABLE_MARKET"
THROTTLE_BETTING = "THROTTLE_BETTING"
INCREASE_CONFIDENCE = "INCREASE_CONFIDENCE"
ALERT_ONLY_MODE = "ALERT_ONLY_MODE"
SEND_ALERT = "SEND_ALERT"
RESET_THROTTLE = "RESET_THROTTLE"
REENABLE_MARKET = "REENABLE_MARKET"


def create_retrain_action(market: str, reason: str, context: dict = None) -> Action:
    """Create a retrain model action."""
    return Action(
        type=RETRAIN_MODEL,
        payload={"market": market, "reason": reason, "context": context or {}}
    )


def create_disable_market_action(market: str, reason: str, threshold: float = -0.05) -> Action:
    """Create a disable market action."""
    return Action(
        type=DISABLE_MARKET,
        payload={"market": market, "reason": reason, "threshold": threshold}
    )


def create_throttle_action(reason: str, max_bets_per_run: int = 3) -> Action:
    """Create a throttle betting action."""
    return Action(
        type=THROTTLE_BETTING,
        payload={"reason": reason, "max_bets_per_run": max_bets_per_run}
    )


def create_increase_confidence_action(market: str, reason: str, roi: float) -> Action:
    """Create an increase confidence action."""
    return Action(
        type=INCREASE_CONFIDENCE,
        payload={"market": market, "reason": reason, "roi": roi}
    )


def create_alert_only_mode_action(reason: str) -> Action:
    """Create an alert-only mode action."""
    return Action(
        type=ALERT_ONLY_MODE,
        payload={"reason": reason}
    )


def create_alert_action(title: str, message: str, severity: str = "warning") -> Action:
    """Create a send alert action."""
    return Action(
        type=SEND_ALERT,
        payload={"title": title, "message": message, "severity": severity}
    )


def create_reset_throttle_action(reason: str) -> Action:
    """Create a reset throttle action."""
    return Action(
        type=RESET_THROTTLE,
        payload={"reason": reason}
    )


def create_reenable_market_action(market: str, reason: str) -> Action:
    """Create a re-enable market action."""
    return Action(
        type=REENABLE_MARKET,
        payload={"market": market, "reason": reason}
    )
