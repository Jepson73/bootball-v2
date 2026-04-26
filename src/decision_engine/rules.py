"""
Decision Engine Rules.

Each rule is a function that takes state and event data,
and returns either None or an Action.
"""

import logging
from typing import Optional, Callable

from src.decision_engine.state import DecisionState
from src.decision_engine.actions import (
    Action,
    create_retrain_action,
    create_disable_market_action,
    create_throttle_action,
    create_increase_confidence_action,
    create_alert_only_mode_action,
    create_alert_action,
    create_reset_throttle_action,
    create_reenable_market_action,
)

logger = logging.getLogger(__name__)


# =========================================================
# Rule 1: Degrading Model Rule
# =========================================================
def rule_degrading_model(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If model trend is degrading with statistically meaningful confidence,
    trigger retraining.
    """
    market = data.get("market")
    if not market:
        return None

    trend = state.market_trends.get(market)
    if not trend:
        return None

    if trend.direction == "degrading" and trend.confidence == "statistically_meaningful":
        logger.info(f"Rule: Degrading model detected for {market}")
        return create_retrain_action(
            market=market,
            reason=f"Model degrading: brier={trend.brier_score:.4f}, direction={trend.direction}",
            context={"brier_score": trend.brier_score, "direction": trend.direction}
        )

    return None


# =========================================================
# Rule 2: Loss Streak Protection
# =========================================================
def rule_loss_streak(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If loss streak >= 5, throttle betting.
    """
    if event_type not in ["bet_settled", "bets_settled"]:
        return None

    loss_streak = state.get_loss_streak()

    if loss_streak >= 5 and not state.throttle_active:
        logger.info(f"Rule: Loss streak detected: {loss_streak}")
        return create_throttle_action(
            reason=f"Loss streak: {loss_streak} consecutive losses",
            max_bets_per_run=3
        )

    if loss_streak == 0 and state.throttle_active:
        logger.info(f"Rule: Loss streak ended, resetting throttle")
        state.deactivate_throttle()
        return create_reset_throttle_action(reason="Loss streak ended")

    return None


# =========================================================
# Rule 3: Low ROI Rule
# =========================================================
def rule_low_roi(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If recent ROI < -5%, disable the market.
    """
    if event_type not in ["bet_settled", "bets_settled"]:
        return None

    market = data.get("market")
    if not market:
        return None

    # Get market-specific recent results
    recent_market = [b for b in state.recent_results if b.market == market]
    if len(recent_market) < 10:
        return None

    total_pnl = sum(b.pnl for b in recent_market)
    if total_pnl < -50:  # -50 SEK threshold
        if not state.is_market_disabled(market):
            logger.info(f"Rule: Low ROI for market {market}: {total_pnl}")
            return create_disable_market_action(
                market=market,
                reason=f"Low ROI: {total_pnl:.2f} SEK in recent bets",
                threshold=-0.05
            )

    return None


# =========================================================
# Rule 4: Strong Performance Rule
# =========================================================
def rule_strong_performance(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If ROI > 15% and sample_size > 100, increase confidence.
    """
    market = data.get("market")
    if not market:
        return None

    trend = state.market_trends.get(market)
    if not trend:
        return None

    if trend.roi > 15 and trend.sample_size > 100:
        logger.info(f"Rule: Strong performance for {market}: ROI={trend.roi}%")
        return create_increase_confidence_action(
            market=market,
            reason=f"Strong ROI: {trend.roi}% over {trend.sample_size} samples",
            roi=trend.roi
        )

    return None


# =========================================================
# Rule 5: Health Degradation Rule
# =========================================================
def rule_health_degradation(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If health status is bad (< 50), enable alert-only mode.
    """
    if event_type != "health_update":
        return None

    health = state.last_health
    if not health:
        return None

    if health.health_score < 50 and not state.alert_only_mode:
        logger.info(f"Rule: Health degraded to {health.health_score}")
        return create_alert_only_mode_action(
            reason=f"Health score: {health.health_score}"
        )

    if health.health_score >= 70 and state.alert_only_mode:
        logger.info("Rule: Health recovered, exiting alert-only mode")
        state.set_alert_only_mode(False)
        return create_alert_action(
            title="Health Recovered",
            message=f"Health score back to {health.health_score}",
            severity="success"
        )

    return None


# =========================================================
# Rule 6: Alert on High Error Rate
# =========================================================
def rule_high_error_rate(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If error rate > 10%, send alert.
    """
    if event_type != "health_update":
        return None

    health = state.last_health
    if not health:
        return None

    if health.error_rate > 0.10:
        return create_alert_action(
            title="High Error Rate",
            message=f"Error rate: {health.error_rate * 100:.1f}%",
            severity="error"
        )

    return None


# =========================================================
# Rule 7: Re-enable Market on Recovery
# =========================================================
def rule_reenable_market(state: DecisionState, event_type: str, data: dict) -> Optional[Action]:
    """
    If market was disabled but now shows positive performance, re-enable.
    """
    market = data.get("market")
    if not market:
        return None

    if not state.is_market_disabled(market):
        return None

    # Check recent performance for this market
    recent_market = [b for b in state.recent_results if b.market == market]
    if len(recent_market) < 20:
        return None

    total_pnl = sum(b.pnl for b in recent_market)
    if total_pnl > 50:  # Positive recovery
        logger.info(f"Rule: Market {market} recovered, re-enabling")
        state.enable_market(market)
        return create_reenable_market_action(
            market=market,
            reason=f"Recovery: {total_pnl:.2f} SEK in recent bets"
        )

    return None


# =========================================================
# All Rules Registry
# =========================================================
def load_rules() -> list[Callable]:
    """Load all rules for evaluation."""
    return [
        rule_degrading_model,
        rule_loss_streak,
        rule_low_roi,
        rule_strong_performance,
        rule_health_degradation,
        rule_high_error_rate,
        rule_reenable_market,
    ]
