"""
Adaptive Allocator - Self-tuning allocation based on performance.

Adjusts allocation weights based on:
- Market ROI
- Trend direction
- Confidence (number of bets)

Emits ALLOCATION_UPDATED events.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events
from src.performance.performance_tracker import get_performance_tracker

logger = logging.getLogger(__name__)


@dataclass
class AllocatorConfig:
    """Configuration for adaptive allocation."""
    min_weight: float = 0.05
    max_weight: float = 0.50
    base_weights: dict[str, float] = field(default_factory=lambda: {
        "h2h": 0.25,
        "btts": 0.25,
        "ou25": 0.25,
        "ou15": 0.25,
    })
    alpha: float = 0.3  # How much to adjust based on ROI
    min_bets_for_adjustment: int = 10
    cooldown_seconds: int = 300


class AdaptiveAllocator:
    """
    Dynamically adjusts allocation weights based on performance.
    """
    
    def __init__(self, config: AllocatorConfig = None):
        self.config = config or AllocatorConfig()
        self._current_weights = self.config.base_weights.copy()
        self._last_update: Optional[datetime] = None
        
        # Subscribe to performance updates
        event_bus.subscribe(Events.PERFORMANCE_UPDATE, self.handle_performance_update)
        
        logger.info("AdaptiveAllocator initialized")
    
    def handle_performance_update(self, event) -> None:
        """Handle performance update and recalculate weights."""
        # Check cooldown
        if self._last_update:
            elapsed = (datetime.utcnow() - self._last_update).total_seconds()
            if elapsed < self.config.cooldown_seconds:
                return
        
        self.recalculate()
    
    def recalculate(self) -> dict[str, float]:
        """Recalculate allocation weights based on current performance."""
        tracker = get_performance_tracker()
        
        # Get current weights
        perf_weights = tracker.get_weights()
        
        # Adjust base weights based on performance
        new_weights = {}
        
        for market in self.config.base_weights.keys():
            base = self.config.base_weights.get(market, 0.25)
            
            # Get performance
            perf = tracker.get_market_performance(market)
            
            if perf and perf.bets >= self.config.min_bets_for_adjustment:
                # Adjust based on ROI
                roi = perf.roi / 100.0  # Convert to decimal
                
                # Adjust weight: weight *= (1 + alpha * roi)
                adjusted = base * (1 + self.config.alpha * roi)
                
                # Apply trend modifier
                if perf.trend == "improving":
                    adjusted *= 1.1
                elif perf.trend == "degrading":
                    adjusted *= 0.9
                
                # Clamp
                adjusted = max(self.config.min_weight, min(self.config.max_weight, adjusted))
                
                new_weights[market] = adjusted
            else:
                # Not enough data, use base
                new_weights[market] = base
        
        # Normalize to sum to 1
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v/total for k, v in new_weights.items()}
        
        self._current_weights = new_weights
        self._last_update = datetime.utcnow()
        
        # Emit update
        self._emit_allocation_update()
        
        # Log
        self._log_weights()
        
        return new_weights
    
    def _emit_allocation_update(self) -> None:
        """Emit allocation update event."""
        event_bus.emit(Events.ALLOCATION_UPDATED, {
            "weights": self._current_weights,
            "timestamp": self._last_update.isoformat() if self._last_update else None
        })
    
    def _log_weights(self) -> None:
        """Log current weights."""
        parts = []
        for market, weight in sorted(self._current_weights.items()):
            parts.append(f"{market}={weight:.0%}")
        
        logger.info(f"[ALLOC] {' '.join(parts)}")
    
    def get_weights(self) -> dict[str, float]:
        """Get current allocation weights."""
        return self._current_weights.copy()
    
    def reset_weights(self) -> None:
        """Reset to base weights."""
        self._current_weights = self.config.base_weights.copy()
        self._emit_allocation_update()
        self._log_weights()


# Global instance
_allocator: Optional[AdaptiveAllocator] = None


def get_adaptive_allocator() -> AdaptiveAllocator:
    """Get global adaptive allocator."""
    global _allocator
    if _allocator is None:
        _allocator = AdaptiveAllocator()
    return _allocator
