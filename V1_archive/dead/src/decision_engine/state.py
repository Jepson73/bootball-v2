"""
Decision Engine State Management.

Maintains rolling state for decision-making:
- Health status
- Market trends
- Recent bet results
- Run history
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    health_score: float = 100.0
    error_rate: float = 0.0
    avg_duration: float = 0.0
    last_updated: Optional[str] = None


@dataclass
class MarketTrend:
    market: str
    direction: str  # "improving", "stable", "degrading"
    confidence: str  # "low", "medium", "statistically_meaningful"
    brier_score: float = 0.0
    sample_size: int = 0
    roi: float = 0.0
    last_updated: Optional[str] = None


@dataclass
class BetResult:
    market: str
    outcome: str
    won: bool
    pnl: float
    odds: float
    ev: float
    settled_at: datetime


class DecisionState:
    """Maintains rolling state for the Decision Engine."""

    def __init__(self, max_recent_results: int = 100):
        self.last_health: Optional[HealthStatus] = None
        self.market_trends: dict[str, MarketTrend] = {}
        self.recent_results: list[BetResult] = []
        self.max_recent_results = max_recent_results
        self.last_run_time: Optional[datetime] = None
        self.throttle_active: bool = False
        self.disabled_markets: set[str] = set()
        self.alert_only_mode: bool = False

    def update_health(self, data: dict) -> None:
        """Update health status from event data."""
        self.last_health = HealthStatus(
            health_score=data.get("health_score", 100.0),
            error_rate=data.get("error_rate", 0.0),
            avg_duration=data.get("avg_duration", 0.0),
            last_updated=datetime.utcnow().isoformat()
        )
        logger.debug(f"Health updated: {self.last_health.health_score}")

    def update_trend(self, data: dict) -> None:
        """Update market trend from model trend event."""
        market = data.get("market")
        if not market:
            return

        trend = MarketTrend(
            market=market,
            direction=data.get("direction", "stable"),
            confidence=data.get("confidence", "low"),
            brier_score=data.get("brier_score", 0.0),
            sample_size=data.get("sample_size", 0),
            roi=data.get("roi", 0.0),
            last_updated=datetime.utcnow().isoformat()
        )
        self.market_trends[market] = trend
        logger.debug(f"Trend updated for {market}: {trend.direction}")

    def record_settlement(self, data: dict) -> None:
        """Record a bet settlement."""
        result = BetResult(
            market=data.get("market", ""),
            outcome=data.get("outcome", ""),
            won=data.get("won", False),
            pnl=data.get("pnl", 0.0),
            odds=data.get("odds", 0.0),
            ev=data.get("ev", 0.0),
            settled_at=datetime.utcnow()
        )
        self.recent_results.append(result)

        # Trim to max size
        if len(self.recent_results) > self.max_recent_results:
            self.recent_results = self.recent_results[-self.max_recent_results:]

        logger.debug(f"Settlement recorded: {result.market} - {'WIN' if result.won else 'LOSS'}")

    def update_run_time(self) -> None:
        """Update last run time."""
        self.last_run_time = datetime.utcnow()

    def get_recent_roi(self, window: int = 50) -> float:
        """Calculate ROI over recent window of settled bets."""
        if not self.recent_results:
            return 0.0

        recent = self.recent_results[-window:]
        if not recent:
            return 0.0

        total_pnl = sum(b.pnl for b in recent)
        total_staked = sum(b.pnl / (b.odds - 1) if b.odds > 1 else b.pnl for b in recent)

        if total_staked == 0:
            return 0.0

        return (total_pnl / total_staked) * 100

    def get_loss_streak(self) -> int:
        """Get current loss streak (consecutive losses from most recent)."""
        streak = 0
        for result in reversed(self.recent_results):
            if not result.won:
                streak += 1
            else:
                break
        return streak

    def get_win_streak(self) -> int:
        """Get current win streak (consecutive wins from most recent)."""
        streak = 0
        for result in reversed(self.recent_results):
            if result.won:
                streak += 1
            else:
                break
        return streak

    def is_market_disabled(self, market: str) -> bool:
        """Check if a market is disabled."""
        return market in self.disabled_markets

    def disable_market(self, market: str) -> None:
        """Disable a market."""
        self.disabled_markets.add(market)
        logger.info(f"Market disabled: {market}")

    def enable_market(self, market: str) -> None:
        """Re-enable a market."""
        self.disabled_markets.discard(market)
        logger.info(f"Market enabled: {market}")

    def activate_throttle(self) -> None:
        """Activate throttling."""
        self.throttle_active = True
        logger.info("Betting throttled")

    def deactivate_throttle(self) -> None:
        """Deactivate throttling."""
        self.throttle_active = False
        logger.info("Betting throttling deactivated")

    def set_alert_only_mode(self, enabled: bool) -> None:
        """Set alert-only mode."""
        self.alert_only_mode = enabled
        logger.info(f"Alert-only mode: {enabled}")

    @property
    def mode(self) -> str:
        """Get current system mode."""
        if self.alert_only_mode:
            return "alert_only"
        if self.throttle_active:
            return "safe"
        return "normal"

    def is_market_allowed(self, market: str) -> bool:
        """Check if market is allowed for betting."""
        if self.alert_only_mode:
            return False
        if market in self.disabled_markets:
            return False
        return True


# Global state instance
_global_state: DecisionState = None


def get_decision_state() -> DecisionState:
    """Get global decision state."""
    global _global_state
    if _global_state is None:
        _global_state = DecisionState()
    return _global_state
