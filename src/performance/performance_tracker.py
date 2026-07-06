"""
Performance Tracker - Tracks per-market betting performance.

Monitors:
- ROI
- Hit rate
- Average EV vs realized return
- Drawdown
- Trend detection

Emits PERFORMANCE_UPDATE events.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.events.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


@dataclass
class MarketPerformance:
    """Performance metrics for a single market."""
    market: str
    bets: int = 0
    wins: int = 0
    losses: int = 0
    roi: float = 0.0
    avg_ev: float = 0.0
    realized_edge: float = 0.0
    drawdown: float = 0.0
    trend: str = "stable"  # improving / stable / degrading
    total_staked: float = 0.0
    total_pnl: float = 0.0
    avg_odds: float = 0.0
    last_updated: Optional[datetime] = None


class PerformanceTracker:
    """
    Tracks performance metrics per market over rolling windows.
    """
    
    def __init__(self, window_size: int = 50, min_bets_for_trend: int = 20):
        self.window_size = window_size
        self.min_bets_for_trend = min_bets_for_trend
        self._market_performance: dict[str, MarketPerformance] = {}
        self._history: list[dict] = []  # Rolling history for EMA
        
        # Subscribe to settlement events
        event_bus.subscribe(Events.BET_SETTLED, self.handle_bet_settled)
        event_bus.subscribe(Events.BETS_SETTLED, self.handle_bets_settled)
        
        logger.info("PerformanceTracker initialized")
    
    def handle_bet_settled(self, event) -> None:
        """Handle single bet settlement."""
        data = event.data if hasattr(event, 'data') else event
        bet = data.get("bet", {})
        
        if bet:
            self._update_market(bet)
    
    def handle_bets_settled(self, event) -> None:
        """Handle batch bet settlements."""
        data = event.data if hasattr(event, 'data') else event
        bets = data.get("bets", data.get("settled_bets", []))
        
        for bet in bets:
            self._update_market(bet)
        
        # Emit performance update
        self._emit_performance_update()
    
    def _update_market(self, bet: dict) -> None:
        """Update metrics for a market."""
        market = bet.get("market", "unknown")
        won = bet.get("won", False)
        pnl = bet.get("pnl", 0)
        stake = bet.get("stake", 0)
        ev = bet.get("ev", 0)
        odds = bet.get("odds", 0)
        
        if market not in self._market_performance:
            self._market_performance[market] = MarketPerformance(market=market)
        
        perf = self._market_performance[market]
        perf.bets += 1
        perf.total_staked += stake
        perf.total_pnl += pnl
        
        if won:
            perf.wins += 1
        else:
            perf.losses += 1
        
        # Calculate metrics
        if perf.total_staked > 0:
            perf.roi = (perf.total_pnl / perf.total_staked) * 100
        
        # Calculate trend after minimum bets
        if perf.bets >= self.min_bets_for_trend:
            perf.trend = self._detect_trend(market)
        
        perf.last_updated = datetime.utcnow()
        
        # Add to history for EMA
        self._history.append({
            "market": market,
            "pnl": pnl,
            "timestamp": datetime.utcnow()
        })
        
        # Trim history
        max_history = self.window_size * 10
        if len(self._history) > max_history:
            self._history = self._history[-max_history:]
    
    def _detect_trend(self, market: str) -> str:
        """Detect trend using recent history."""
        market_history = [
            h for h in self._history[-self.window_size:]
            if h["market"] == market
        ]
        
        if len(market_history) < self.min_bets_for_trend:
            return "stable"
        
        # Split into halves
        half = len(market_history) // 2
        first_half = market_history[:half]
        second_half = market_history[half:]
        
        # Calculate PnL for each half
        first_pnl = sum(h["pnl"] for h in first_half)
        second_pnl = sum(h["pnl"] for h in second_half)
        
        # Simple trend detection
        if second_pnl > first_pnl * 1.2:  # 20% improvement
            return "improving"
        elif second_pnl < first_pnl * 0.8:  # 20% degradation
            return "degrading"
        else:
            return "stable"
    
    def _emit_performance_update(self) -> None:
        """Emit performance update event."""
        metrics = self.get_all()
        
        event_bus.emit(Events.PERFORMANCE_UPDATE, {
            "markets": {
                market: {
                    "bets": perf.bets,
                    "wins": perf.wins,
                    "roi": perf.roi,
                    "trend": perf.trend,
                    "total_pnl": perf.total_pnl
                }
                for market, perf in metrics.items()
            },
            "timestamp": datetime.utcnow().isoformat()
        })
        
        logger.info(f"[PERF] Performance update emitted: {len(metrics)} markets")
    
    def get_market_performance(self, market: str) -> Optional[MarketPerformance]:
        """Get performance for a specific market."""
        return self._market_performance.get(market)
    
    def get_all(self) -> dict[str, MarketPerformance]:
        """Get all market performance."""
        return self._market_performance.copy()
    
    def get_weights(self) -> dict[str, float]:
        """Get current allocation weights based on performance."""
        weights = {}
        
        # Equal base weight
        markets = list(self._market_performance.keys())
        if not markets:
            return {"h2h": 0.25, "btts": 0.25, "ou25": 0.25, "ou15": 0.25}
        
        base = 1.0 / len(markets)
        
        for market, perf in self._market_performance.items():
            if perf.bets < 5:
                # Not enough data, use base weight
                weights[market] = base
            else:
                # Adjust based on ROI
                roi_factor = 1.0 + (perf.roi / 100.0)  # e.g., 1.05 for 5% ROI
                weights[market] = base * roi_factor
        
        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: v/total for k, v in weights.items()}
        
        return weights


# Global instance
_tracker: Optional[PerformanceTracker] = None


def get_performance_tracker() -> PerformanceTracker:
    """Get global performance tracker."""
    global _tracker
    if _tracker is None:
        _tracker = PerformanceTracker()
    return _tracker
