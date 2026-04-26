"""
Self-Tuning Optimizer - updates allocation weights based on performance.

Implements exponential learning:
w_new = w_old + η * (performance_signal - baseline)

where η = learning rate (small, e.g. 0.05)
"""

import logging
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


LEARNING_STATE_FILE = Path("/opt/projects/bootball/data/learning_state.json")


@dataclass
class MarketLearningState:
    """Learning state for a single market."""
    market: str
    weight: float
    performance_history: list = None
    stability_factor: float = 1.0
    regime_consistency: float = 1.0
    
    def __post_init__(self):
        if self.performance_history is None:
            self.performance_history = []


class WeightOptimizer:
    """
    Self-tuning optimizer that updates allocation weights based on performance.
    """
    
    # Learning parameters
    LEARNING_RATE = 0.05  # Small to avoid overreaction
    MIN_WEIGHT = 0.05
    MAX_WEIGHT = 0.50
    BASELINE_ROI = 0.02  # 2% baseline ROI
    
    DEFAULT_WEIGHTS = {
        "h2h": 0.25,
        "btts": 0.25,
        "ou25": 0.25,
        "ou15": 0.25,
    }
    
    def __init__(self):
        self._market_states: Dict[str, MarketLearningState] = {}
        self._load_state()
        
    def _load_state(self) -> None:
        """Load learning state from file."""
        if LEARNING_STATE_FILE.exists():
            try:
                data = json.loads(LEARNING_STATE_FILE.read_text())
                for market, state in data.get("markets", {}).items():
                    self._market_states[market] = MarketLearningState(
                        market=market,
                        weight=state.get("weight", 0.25),
                        performance_history=state.get("performance_history", []),
                        stability_factor=state.get("stability_factor", 1.0),
                        regime_consistency=state.get("regime_consistency", 1.0),
                    )
                logger.info(f"[OPTIMIZER] Loaded state for {len(self._market_states)} markets")
            except Exception as e:
                logger.warning(f"[OPTIMIZER] Failed to load state: {e}")
                self._init_default_states()
        else:
            self._init_default_states()
    
    def _init_default_states(self) -> None:
        """Initialize default states."""
        for market, weight in self.DEFAULT_WEIGHTS.items():
            self._market_states[market] = MarketLearningState(
                market=market,
                weight=weight,
            )
        self._save_state()
    
    def _save_state(self) -> None:
        """Save learning state to file."""
        LEARNING_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "markets": {
                market: {
                    "weight": state.weight,
                    "performance_history": state.performance_history[-20:],  # Keep last 20
                    "stability_factor": state.stability_factor,
                    "regime_consistency": state.regime_consistency,
                }
                for market, state in self._market_states.items()
            },
            "last_updated": datetime.utcnow().isoformat(),
        }
        
        LEARNING_STATE_FILE.write_text(json.dumps(data, indent=2))
    
    def optimize(
        self,
        performance_eval: dict,
        current_weights: Dict[str, float] = None
    ) -> Dict[str, float]:
        """
        Update weights based on performance.
        
        Args:
            performance_eval: Output from PerformanceEvaluator
            current_weights: Current allocation weights
            
        Returns:
            Updated weights dict
        """
        logger.info("[OPTIMIZER] Updating weights based on performance")
        
        current_weights = current_weights or self.DEFAULT_WEIGHTS.copy()
        
        # Get market performance
        market_perf = performance_eval.get("market_performance", {})
        
        # Update each market's learning state
        for market, perf in market_perf.items():
            if market not in self._market_states:
                self._market_states[market] = MarketLearningState(
                    market=market,
                    weight=current_weights.get(market, 0.25),
                )
            
            state = self._market_states[market]
            
            # Record performance
            roi = perf.get("roi", 0)
            state.performance_history.append(roi)
            
            # Compute stability factor (volatility of returns)
            if len(state.performance_history) >= 5:
                recent = state.performance_history[-10:]
                variance = sum((r - sum(recent)/len(recent))**2 for r in recent) / len(recent)
                state.stability_factor = max(0.5, 1.0 - variance)  # Higher = more stable
        
        # Compute new weights using exponential learning
        new_weights = self._compute_new_weights(current_weights, market_perf)
        
        # Normalize weights
        new_weights = self._normalize_weights(new_weights)
        
        # Update states
        for market, weight in new_weights.items():
            if market in self._market_states:
                self._market_states[market].weight = weight
        
        # Save state
        self._save_state()
        
        logger.info(f"[OPTIMIZER] Updated weights: {new_weights}")
        
        return new_weights
    
    def _compute_new_weights(
        self,
        current_weights: Dict[str, float],
        market_perf: Dict[str, dict]
    ) -> Dict[str, float]:
        """Compute new weights using exponential learning."""
        new_weights = {}
        
        for market, current_weight in current_weights.items():
            perf = market_perf.get(market, {})
            roi = perf.get("roi", 0)
            
            # Performance signal: ROI vs baseline
            perf_signal = roi - self.BASELINE_ROI
            
            # Update rule: w_new = w_old + η * perf_signal * stability
            state = self._market_states.get(market)
            stability = state.stability_factor if state else 1.0
            
            delta = self.LEARNING_RATE * perf_signal * stability * current_weight
            
            new_weight = current_weight + delta
            
            # Clamp to bounds
            new_weight = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, new_weight))
            new_weights[market] = new_weight
        
        return new_weights
    
    def _normalize_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Normalize weights using softmax."""
        # Compute softmax
        values = [weights.get(m, 0.1) for m in ["h2h", "btts", "ou25", "ou15"]]
        exp_values = [math.exp(v * 3) for v in values]  # Temperature = 3
        total = sum(exp_values)
        
        if total == 0:
            return self.DEFAULT_WEIGHTS.copy()
        
        softmax_weights = {m: exp_values[i] / total for i, m in enumerate(["h2h", "btts", "ou25", "ou15"])}
        
        # Ensure all markets are present
        for m in ["h2h", "btts", "ou25", "ou15"]:
            if m not in softmax_weights:
                softmax_weights[m] = 0.1
        
        return softmax_weights
    
    def get_weights(self) -> Dict[str, float]:
        """Get current weights."""
        return {m: s.weight for m, s in self._market_states.items()}
    
    def get_market_score(self, market: str) -> float:
        """Get composite score for a market."""
        state = self._market_states.get(market)
        if not state:
            return 0.0
        
        # Score = ROI * stability * regime_consistency
        recent_roi = sum(state.performance_history[-5:]) / max(len(state.performance_history[-5:]), 1)
        
        return recent_roi * state.stability_factor * state.regime_consistency
    
    def reset(self) -> None:
        """Reset learning state to defaults."""
        self._init_default_states()
        logger.info("[OPTIMIZER] Reset to default state")


# Global optimizer
_optimizer: Optional[WeightOptimizer] = None


def get_weight_optimizer() -> WeightOptimizer:
    """Get global weight optimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = WeightOptimizer()
    return _optimizer
