"""
Self-Optimizing Allocator - Uses PortfolioState for self-tuning.

This allocator uses the stateful PortfolioState to calculate weights
based on drawdown, volatility, regime, and historical ROI trends.

Emits ALLOCATION_UPDATED events.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events
from src.portfolio.state.portfolio_state import PortfolioState
from src.portfolio.adaptive_allocator import get_adaptive_allocator

logger = logging.getLogger(__name__)


@dataclass
class SelfOptimizingConfig:
    """Configuration for self-optimizing allocation."""
    min_weight: float = 0.05
    max_weight: float = 0.50
    base_weights: dict = field(default_factory=lambda: {
        "h2h": 0.25,
        "btts": 0.25,
        "ou25": 0.25,
        "ou15": 0.25,
    })
    drawdown_penalty: float = 2.0
    volatility_penalty: float = 1.5
    regime_bull_mult: float = 1.2
    regime_defensive_mult: float = 0.5
    trend_window: int = 10


class SelfOptimizingAllocator:
    """
    State-aware adaptive allocator.
    
    Uses PortfolioState to calculate weights based on:
    - Current drawdown
    - Current volatility
    - Current regime
    - Historical ROI trends
    - Current exposure
    """
    
    def __init__(self, config: SelfOptimizingConfig = None):
        self.config = config or SelfOptimizingConfig()
        self._current_weights = self.config.base_weights.copy()
        self._last_update: Optional[datetime] = None
        self._fallback = get_adaptive_allocator()
        
        logger.info("[SELF_OPT] SelfOptimizingAllocator initialized")
    
    def calculate_weights(self, state: PortfolioState) -> dict:
        """
        Calculate allocation weights using PortfolioState.
        
        Args:
            state: PortfolioState with historical data
            
        Returns:
            Market weights dict
        """
        if state is None:
            logger.warning("[SELF_OPT] No state provided, using fallback")
            return self._fallback.get_weights()
        
        logger.info(f"[SELF_OPT] Calculating weights from state (run {state.run_count})")
        
        # Get base weights
        new_weights = self.config.base_weights.copy()
        
        # Apply drawdown penalty
        if state.drawdown > 0:
            drawdown_factor = 1.0 - (state.drawdown * self.config.drawdown_penalty)
            new_weights = {k: v * drawdown_factor for k, v in new_weights.items()}
            logger.info(f"[SELF_OPT] Drawdown penalty: {drawdown_factor:.2f}")
        
        # Apply volatility penalty
        if state.volatility > 0:
            vol_factor = 1.0 - (state.volatility * self.config.volatility_penalty)
            new_weights = {k: v * vol_factor for k, v in new_weights.items()}
            logger.info(f"[SELF_OPT] Volatility penalty: {vol_factor:.2f}")
        
        # Apply regime adjustment
        regime_mult = self.config.regime_bull_mult if state.regime == "bull" else (
            self.config.regime_defensive_mult if state.regime == "defensive" else 1.0
        )
        new_weights = {k: v * regime_mult for k, v in new_weights.items()}
        logger.info(f"[SELF_OPT] Regime adjustment: {regime_mult:.2f}")
        
        # Apply lambda scaling
        lambda_factor = 1.0 / state.risk_lambda if state.risk_lambda > 0 else 1.0
        new_weights = {k: v * lambda_factor for k, v in new_weights.items()}
        logger.info(f"[SELF_OPT] Lambda scaling: {lambda_factor:.2f}")
        
        # Apply ROI trend from historical data
        if state.historical_roi and len(state.historical_roi) >= self.config.trend_window:
            recent_roi = state.historical_roi[-self.config.trend_window:]
            avg_roi = sum(recent_roi) / len(recent_roi)
            
            if avg_roi > 0.05:
                for market in new_weights:
                    new_weights[market] *= 1.15
            elif avg_roi > 0:
                for market in new_weights:
                    new_weights[market] *= 1.05
            elif avg_roi < -0.05:
                for market in new_weights:
                    new_weights[market] *= 0.85
            elif avg_roi < 0:
                for market in new_weights:
                    new_weights[market] *= 0.95
            
            logger.info(f"[SELF_OPT] ROI adjustment: avg_roi={avg_roi:.2%}")
        
        # Apply exposure constraints from state
        if state.exposure_by_market:
            for market, exposure in state.exposure_by_market.items():
                if exposure > 0.30:
                    new_weights[market] *= 0.7
                    logger.info(f"[SELF_OPT] High exposure penalty for {market}: {exposure:.0%}")
        
        # Clamp weights
        new_weights = {
            k: max(self.config.min_weight, min(self.config.max_weight, v))
            for k, v in new_weights.items()
        }
        
        # Normalize to sum to 1
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v/total for k, v in new_weights.items()}
        
        self._current_weights = new_weights
        self._last_update = datetime.utcnow()
        
        self._emit_allocation_update(state.run_count)
        self._log_weights()
        
        return new_weights
    
    def calculate_weights_hybrid(self, state: PortfolioState) -> dict:
        """
        Hybrid mode: combine stateful and adaptive allocator.
        
        Uses:
        - PortfolioState for current drawdown/volatility/regime
        - AdaptiveAllocator for historical performance trends
        """
        # Get stateful weights
        stateful = self.calculate_weights(state)
        
        # Get fallback adaptive weights
        adaptive = self._fallback.get_weights()
        
        # Blend: 60% stateful, 40% adaptive
        blended = {}
        all_markets = set(stateful.keys()) | set(adaptive.keys())
        
        for market in all_markets:
            s = stateful.get(market, 0.25)
            a = adaptive.get(market, 0.25)
            blended[market] = 0.6 * s + 0.4 * a
        
        # Normalize
        total = sum(blended.values())
        if total > 0:
            blended = {k: v/total for k, v in blended.items()}
        
        self._current_weights = blended
        self._log_weights()
        
        return blended
    
    def get_weights(self) -> dict:
        """Get current allocation weights."""
        return self._current_weights.copy()
    
    def _emit_allocation_update(self, run_count: int) -> None:
        """Emit allocation update event."""
        event_bus.emit(Events.ALLOCATION_UPDATED, {
            "weights": self._current_weights,
            "run_count": run_count,
            "timestamp": self._last_update.isoformat() if self._last_update else None,
            "source": "self_optimizing"
        })
    
    def _log_weights(self) -> None:
        """Log current weights."""
        parts = []
        for market, weight in sorted(self._current_weights.items()):
            parts.append(f"{market}={weight:.0%}")
        
        logger.info(f"[SELF_OPT] {' '.join(parts)}")


# Global instance
_allocator: Optional[SelfOptimizingAllocator] = None


def get_self_optimizing_allocator() -> SelfOptimizingAllocator:
    """Get global self-optimizing allocator."""
    global _allocator
    if _allocator is None:
        _allocator = SelfOptimizingAllocator()
    return _allocator
