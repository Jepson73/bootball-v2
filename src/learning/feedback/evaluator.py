"""
Performance Evaluator - computes metrics from execution results.

Analyzes:
- realized ROI
- expected vs actual EV gap
- calibration drift
- market-level performance
- regime-conditioned returns
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MarketPerformance:
    """Performance metrics for a single market."""
    market: str
    total_bets: int = 0
    wins: int = 0
    total_stake: float = 0.0
    total_pnl: float = 0.0
    roi: float = 0.0
    avg_ev: float = 0.0
    realized_ev: float = 0.0


@dataclass
class RegimePerformance:
    """Performance metrics for a regime."""
    regime: str
    total_runs: int = 0
    total_pnl: float = 0.0
    roi: float = 0.0


class PerformanceEvaluator:
    """
    Evaluates execution performance and computes learning signals.
    """
    
    def __init__(self):
        self._history: List[dict] = []
        
    def evaluate(
        self,
        bets: List[dict],
        predictions: List[dict],
        risk_profile: dict,
        previous_weights: dict = None
    ) -> dict:
        """
        Evaluate performance from a run.
        
        Args:
            bets: List of placed bets with outcomes
            predictions: List of predictions made
            risk_profile: Risk profile used
            previous_weights: Previous allocation weights
            
        Returns:
            Performance evaluation dict
        """
        if not bets:
            logger.warning("[EVALUATOR] No bets to evaluate")
            return self._empty_evaluation()
        
        logger.info(f"[EVALUATOR] Evaluating {len(bets)} bets")
        
        # Compute overall metrics
        total_stake = sum(b.get("stake", 0) for b in bets)
        total_pnl = sum(b.get("pnl", 0) for b in bets if b.get("pnl") is not None)
        overall_roi = total_pnl / total_stake if total_stake > 0 else 0
        
        # Compute market-level performance
        market_perf = self._compute_market_performance(bets)
        
        # Compute EV realization ratio
        ev_realization = self._compute_ev_realization(bets, predictions)
        
        # Compute regime performance
        regime_perf = self._compute_regime_performance(risk_profile, total_pnl, total_stake)
        
        # Find best/worst markets
        sorted_markets = sorted(market_perf.values(), key=lambda x: x.roi, reverse=True)
        best_markets = [m.market for m in sorted_markets[:2] if m.total_bets >= 3]
        worst_markets = [m.market for m in sorted_markets[-2:] if m.total_bets >= 3]
        
        result = {
            "overall_roi": overall_roi,
            "total_bets": len(bets),
            "total_stake": total_stake,
            "total_pnl": total_pnl,
            "ev_realization_ratio": ev_realization,
            "best_markets": best_markets,
            "worst_markets": worst_markets,
            "market_performance": {
                m: {
                    "roi": mp.roi,
                    "total_bets": mp.total_bets,
                    "win_rate": mp.wins / mp.total_bets if mp.total_bets > 0 else 0,
                    "realized_ev": mp.realized_ev,
                }
                for m, mp in market_perf.items()
            },
            "regime_performance": regime_perf,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._history.append(result)
        logger.info(f"[EVALUATOR] ROI: {overall_roi:.2%}, EV realization: {ev_realization:.2%}")
        
        return result
    
    def _compute_market_performance(self, bets: List[dict]) -> Dict[str, MarketPerformance]:
        """Compute performance per market."""
        markets: Dict[str, MarketPerformance] = {}
        
        for bet in bets:
            market = bet.get("market", "unknown")
            if market not in markets:
                markets[market] = MarketPerformance(market=market)
            
            mp = markets[market]
            mp.total_bets += 1
            
            if bet.get("won"):
                mp.wins += 1
            
            stake = bet.get("stake", 0)
            mp.total_stake += stake
            
            pnl = bet.get("pnl", 0)
            if pnl is not None:
                mp.total_pnl += pnl
            
            # Compute EV contribution
            ev = bet.get("ev", 0)
            if ev:
                mp.avg_ev += ev
        
        # Compute final metrics
        for mp in markets.values():
            if mp.total_stake > 0:
                mp.roi = mp.total_pnl / mp.total_stake
            if mp.total_bets > 0:
                mp.avg_ev /= mp.total_bets
                mp.realized_ev = mp.total_pnl / (mp.total_stake * mp.total_bets) if mp.total_stake > 0 else 0
        
        return markets
    
    def _compute_ev_realization(self, bets: List[dict], predictions: List[dict]) -> float:
        """Compute ratio of realized EV vs expected."""
        if not bets or not predictions:
            return 0.0
        
        expected_ev = sum(b.get("ev", 0) for b in bets if b.get("ev"))
        if expected_ev == 0:
            return 0.0
        
        # Actual return as proxy
        total_stake = sum(b.get("stake", 0) for b in bets)
        actual_pnl = sum(b.get("pnl", 0) for b in bets if b.get("pnl") is not None)
        
        # EV realization = actual / expected
        # If > 1, we outperformed EV; if < 1, underperformed
        realized = actual_pnl / (expected_ev * total_stake) if total_stake > 0 else 0
        
        return realized
    
    def _compute_regime_performance(self, risk_profile: dict, pnl: float, stake: float) -> dict:
        """Compute performance per regime."""
        regime = risk_profile.get("regime", "neutral") if risk_profile else "neutral"
        roi = pnl / stake if stake > 0 else 0
        
        return {
            regime: {
                "roi": roi,
                "pnl": pnl,
                "runs": 1
            }
        }
    
    def _empty_evaluation(self) -> dict:
        """Return empty evaluation."""
        return {
            "overall_roi": 0.0,
            "total_bets": 0,
            "total_stake": 0.0,
            "total_pnl": 0.0,
            "ev_realization_ratio": 0.0,
            "best_markets": [],
            "worst_markets": [],
            "market_performance": {},
            "regime_performance": {},
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def get_history(self) -> List[dict]:
        """Get performance history."""
        return self._history
    
    def get_trend(self, metric: str, window: int = 10) -> float:
        """Compute trend for a metric over window."""
        if len(self._history) < 2:
            return 0.0
        
        recent = self._history[-window:]
        values = [r.get(metric, 0) for r in recent if metric in r]
        
        if len(values) < 2:
            return 0.0
        
        # Simple linear trend
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        slope = numerator / denominator
        return slope


# Global evaluator
_evaluator: Optional[PerformanceEvaluator] = None


def get_performance_evaluator() -> PerformanceEvaluator:
    """Get global performance evaluator."""
    global _evaluator
    if _evaluator is None:
        _evaluator = PerformanceEvaluator()
    return _evaluator
