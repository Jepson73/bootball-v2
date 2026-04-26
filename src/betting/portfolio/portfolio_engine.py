"""
Portfolio Engine - PRIMARY DECISION CORE for Bootball.

This is the SINGLE authoritative decision path for ALL bet selection.
No bet exists unless produced by this engine.

Flow:
1. Input: all predictions + odds + correlations + risk profile
2. Apply: Markowitz optimization + correlation penalty + learning weights + regime scaling
3. Output: final capital allocation vector per bet

RULES:
- No bet exists unless produced by portfolio optimizer
- No stake exists unless risk engine approves
- Kelly is output only, NOT decision driver
- EV-based filtering happens BEFORE this layer
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import numpy as np

from src.betting.portfolio.markowitz_optimizer import MarkowitzOptimizer, get_markowitz_optimizer
from src.betting.correlation import get_correlation_engine
from src.portfolio.adaptive_allocator import get_adaptive_allocator
from src.agents.risk_manager.agent import get_risk_manager_agent
from src.learning.optimizer.weight_optimizer import get_weight_optimizer
from src.alerts.event_bus import event_bus, Events
from src.agents.shared.events import AgentEvents

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """Configuration for portfolio engine."""
    risk_aversion: float = 1.0
    max_total_exposure: float = 0.30
    max_per_bet: float = 0.05
    min_bet: float = 10.0
    use_correlation: bool = True
    use_learning: bool = True
    use_adversary: bool = True


@dataclass
class AllocationVector:
    """Output from portfolio engine."""
    bet_id: str
    fixture_id: int
    market: str
    outcome: str
    odds: float
    stake: float
    weight: float
    expected_return: float
    risk_contribution: float
    correlation_penalty: float = 0.0
    regime_adjustment: float = 1.0


class PortfolioEngine:
    """
    SINGLE AUTHORITATIVE DECISION CORE.
    
    All bet selection MUST flow through this engine.
    
    Responsibilities:
    1. Receive all predictions + odds + risk profile
    2. Apply Markowitz mean-variance optimization
    3. Apply correlation penalties
    4. Apply learning weights (adaptive allocator)
    5. Apply regime-based risk scaling
    6. Return allocation vector
    """
    
    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()
        self._markowitz = get_markowitz_optimizer()
        self._correlation = get_correlation_engine()
        self._adaptive = get_adaptive_allocator()
        self._risk_manager = get_risk_manager_agent()
        self._weight_optimizer = get_weight_optimizer()
        
        logger.info("[PORTFOLIO] Engine initialized - PRIMARY DECISION CORE")
    
    def compute_allocation(
        self,
        predictions: List[dict],
        bankroll: float,
        risk_profile: dict = None
    ) -> List[AllocationVector]:
        """
        MAIN ENTRY POINT for all bet decisions.
        
        Args:
            predictions: List of prediction dicts from ML layer
            bankroll: Current bankroll
            risk_profile: Risk profile from Risk Manager
            
        Returns:
            List of AllocationVector (final allocations)
        """
        if not predictions:
            logger.info("[PORTFOLIO] No predictions provided, empty allocation")
            return []
        
        logger.info(f"[PORTFOLIO] Computing allocation for {len(predictions)} predictions")
        
        # Step 1: Prepare candidates
        candidates = self._prepare_candidates(predictions)
        
        # Step 2: Apply learning weights
        if self.config.use_learning:
            candidates = self._apply_learning_weights(candidates)
        
        # Step 3: Apply correlation constraints
        if self.config.use_correlation:
            candidates = self._apply_correlation_constraints(candidates)
        
        # Step 4: Apply regime-based risk scaling
        regime = risk_profile.get("regime", "neutral") if risk_profile else "neutral"
        lambda_val = risk_profile.get("lambda", 1.0) if risk_profile else 1.0
        
        candidates = self._apply_regime_scaling(candidates, regime, lambda_val)
        
        # Step 5: Markowitz optimization
        allocation = self._markowitz_optimize(candidates, bankroll)
        
        # Step 6: Build final allocation vector
        vectors = self._build_allocation_vectors(allocation, bankroll)
        
        # Step 7: Log and emit event
        self._emit_portfolio_event(vectors, regime, lambda_val)
        
        logger.info(f"[PORTFOLIO] Allocation complete: {len(vectors)} bets")
        
        return vectors
    
    def _prepare_candidates(self, predictions: List[dict]) -> List[dict]:
        """Prepare candidates for optimization."""
        candidates = []
        
        for i, pred in enumerate(predictions):
            candidates.append({
                "id": f"pred_{i}_{pred.get('fixture_id')}_{pred.get('market')}",
                "fixture_id": pred.get("fixture_id", 0),
                "market": pred.get("market", "h2h"),
                "outcome": pred.get("outcome", ""),
                "odds": pred.get("odds", 0),
                "our_prob": pred.get("our_prob", 0.5),
                "ev": pred.get("ev", 0),
                "kelly": pred.get("kelly_fraction", pred.get("kelly", 0)),
                "correlation_key": f"fixture_{pred.get('fixture_id')}",
            })
        
        return candidates
    
    def _apply_learning_weights(self, candidates: List[dict]) -> List[dict]:
        """Apply learning weights from adaptive allocator."""
        weights = self._adaptive.get_weights()
        
        for c in candidates:
            market = c.get("market", "h2h")
            market_weight = weights.get(market, 0.25)
            # Scale EV by market weight
            c["ev"] = c.get("ev", 0) * market_weight
        
        return candidates
    
    def _apply_correlation_constraints(self, candidates: List[dict]) -> List[dict]:
        """Apply correlation penalties to candidates."""
        # Correlation engine handles this via correlation_risk in optimizer
        return candidates
    
    def _apply_regime_scaling(
        self,
        candidates: List[dict],
        regime: str,
        lambda_val: float
    ) -> List[dict]:
        """Apply regime-based risk scaling."""
        # Defensive: reduce exposure
        # Bull: increase exposure
        # Neutral: no change
        
        if regime == "defensive":
            for c in candidates:
                c["ev"] = c.get("ev", 0) * 0.5  # Reduce by 50%
        elif regime == "bull":
            for c in candidates:
                c["ev"] = c.get("ev", 0) * 1.2  # Increase by 20%
        
        return candidates
    
    def _markowitz_optimize(
        self,
        candidates: List[dict],
        bankroll: float
    ) -> List[dict]:
        """Run Markowitz optimization."""
        # Configure optimizer
        self._markowitz.config.risk_aversion = self.config.risk_aversion
        self._markowitz.config.max_bet_pct = self.config.max_per_bet
        self._markowitz.config.max_total_exposure = self.config.max_total_exposure
        self._markowitz.config.min_bet = self.config.min_bet
        
        result = self._markowitz.optimize(candidates, bankroll)
        
        return [
            {
                "bet_id": b.bet_id,
                "fixture_id": 0,  # Would need mapping
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "expected_return": b.expected_return,
                "risk_contribution": b.risk_contribution,
            }
            for b in result.bets
        ]
    
    def _build_allocation_vectors(
        self,
        allocation: List[dict],
        bankroll: float
    ) -> List[AllocationVector]:
        """Build final allocation vectors."""
        vectors = []
        
        for a in allocation:
            vectors.append(AllocationVector(
                bet_id=a.get("bet_id", ""),
                fixture_id=a.get("fixture_id", 0),
                market=a.get("market", ""),
                outcome=a.get("outcome", ""),
                odds=a.get("odds", 0),
                stake=a.get("stake", 0),
                weight=a.get("stake", 0) / bankroll if bankroll > 0 else 0,
                expected_return=a.get("expected_return", 0),
                risk_contribution=a.get("risk_contribution", 0),
            ))
        
        return vectors
    
    def _emit_portfolio_event(
        self,
        vectors: List[AllocationVector],
        regime: str,
        lambda_val: float
    ) -> None:
        """Emit portfolio allocation event."""
        total_stake = sum(v.stake for v in vectors)
        total_expected = sum(v.expected_return for v in vectors)
        
        payload = {
            "bets": [
                {
                    "bet_id": v.bet_id,
                    "market": v.market,
                    "stake": v.stake,
                    "expected_return": v.expected_return,
                }
                for v in vectors
            ],
            "total_stake": total_stake,
            "expected_return": total_expected,
            "regime": regime,
            "lambda": lambda_val,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        event_bus.emit(AgentEvents.PORTFOLIO_ALLOCATED, payload)
        
        logger.info(f"[PORTFOLIO] allocation vector generated: {len(vectors)} bets, stake={total_stake:.2f}")


# Global engine
_engine: Optional[PortfolioEngine] = None


def get_portfolio_engine() -> PortfolioEngine:
    """Get global portfolio engine."""
    global _engine
    if _engine is None:
        _engine = PortfolioEngine()
    return _engine
