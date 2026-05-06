"""
Portfolio Engine - PRIMARY DECISION CORE for Bootball.

STATEFUL VERSION - Reads from and writes to PortfolioState.

Flow:
1. Input: predictions + PortfolioState + risk profile
2. Apply: Markowitz optimization + correlation + learning + regime scaling
3. Output: NEW PortfolioState with allocation vector

RULES:
- No bet exists unless produced by portfolio optimizer
- No stake exists unless risk engine approves
- Kelly is output only, NOT decision driver
- State transitions are deterministic
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import numpy as np

from src.betting.portfolio.markowitz_optimizer import MarkowitzOptimizer, get_markowitz_optimizer
from src.betting.correlation import get_correlation_engine
from src.betting.portfolio.cvxpy_optimizer import CVXPYMarkowitzOptimizer, is_cvxpy_available
from src.portfolio.adaptive_allocator import get_adaptive_allocator
from src.agents.risk_manager.agent import get_risk_manager_agent
from src.learning.optimizer.weight_optimizer import get_weight_optimizer
from src.portfolio.state.portfolio_state import PortfolioState
from src.portfolio.state.state_manager import get_state_manager
from src.alerts.event_bus import event_bus, Events
from src.agents.shared.events import AgentEvents

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """Configuration for portfolio engine."""
    risk_aversion: float = 1.0
    max_total_exposure: float = 0.25
    max_per_bet: float = 0.05
    min_bet: float = 10.0
    use_correlation: bool = True
    use_learning: bool = True
    use_adversary: bool = True
    use_cvxpy: bool = True


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
    ev: float = 0.0
    our_prob: float = 0.5


class PortfolioEngine:
    """
    SINGLE AUTHORITATIVE DECISION CORE - STATEFUL VERSION.
    
    All bet selection MUST flow through this engine.
    Reads from and writes to PortfolioState.
    
    Responsibilities:
    1. Load previous PortfolioState
    2. Receive predictions + risk profile
    3. Apply Markowitz mean-variance optimization
    4. Apply correlation penalties
    5. Apply learning weights (adaptive allocator)
    6. Apply regime-based risk scaling
    7. Return NEW PortfolioState with allocation vector
    """
    
    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()
        self._markowitz = get_markowitz_optimizer()
        self._cvxpy_optimizer = CVXPYMarkowitzOptimizer(max_weight=config.max_per_bet if config else 0.5)
        self._correlation = get_correlation_engine()
        self._adaptive = get_adaptive_allocator()
        self._risk_manager = get_risk_manager_agent()
        self._weight_optimizer = get_weight_optimizer()
        self._state_manager = get_state_manager()
        
        self._optimizer_backend = "none"
        self._optimization_status = "not_run"
        self._last_solver_error = None
        
        logger.info("[PORTFOLIO] Engine initialized - STATEFUL PRIMARY DECISION CORE")
        if is_cvxpy_available():
            logger.info("[PORTFOLIO] CVXPY available - using dual-mode optimization")
        else:
            logger.info("[PORTFOLIO] CVXPY unavailable - using heuristic fallback")
    
    def get_optimizer_metrics(self) -> dict:
        """Get optimizer metrics for governance."""
        cvxpy_metrics = self._cvxpy_optimizer.get_metrics() if hasattr(self, '_cvxpy_optimizer') else {}
        
        return {
            "optimizer_backend": self._optimizer_backend,
            "optimization_status": self._optimization_status,
            "solver_error": self._last_solver_error,
            "cvxpy_available": cvxpy_metrics.get("cvxpy_available", False),
            "cvxpy_version": cvxpy_metrics.get("cvxpy_version"),
            "last_cvxpy_status": cvxpy_metrics.get("last_status"),
            "last_objective_value": float(cvxpy_metrics.get("last_objective_value", 0)) if cvxpy_metrics.get("last_objective_value") is not None else None,
        }
    
    def compute_allocation(
        self,
        predictions: List[dict],
        bankroll: float,
        risk_profile: dict = None,
        previous_state: PortfolioState = None
    ) -> tuple[List[AllocationVector], PortfolioState]:
        """
        MAIN ENTRY POINT for all bet decisions - STATEFUL VERSION.
        
        Args:
            predictions: List of prediction dicts from ML layer
            bankroll: Current bankroll
            risk_profile: Risk profile from Risk Manager
            previous_state: Previous PortfolioState (optional, loads if not provided)
            
        Returns:
            Tuple of:
            - List of AllocationVector (final allocations)
            - NEW PortfolioState with updated allocations
        """
        # Load previous state if not provided
        if previous_state is None:
            previous_state = self._state_manager.load_previous_state()
        
        logger.info(f"[PORTFOLIO] Computing allocation with state from run {previous_state.run_count}")
        
        if not predictions:
            logger.info("[PORTFOLIO] No predictions provided, empty allocation")
            return [], previous_state.copy()
        
        logger.info(f"[PORTFOLIO] Computing allocation for {len(predictions)} predictions")
        
        # Step 1: Prepare candidates
        candidates = self._prepare_candidates(predictions)
        
        # Step 2: Apply learning weights from state
        if self.config.use_learning:
            candidates = self._apply_learning_weights(candidates, previous_state)
        
        # Step 3: Apply correlation constraints
        if self.config.use_correlation:
            candidates = self._apply_correlation_constraints(candidates)
        
        # Step 4: Apply regime-based risk scaling
        regime = risk_profile.get("regime", "neutral") if risk_profile else "neutral"
        lambda_val = risk_profile.get("lambda", 1.0) if risk_profile else 1.0
        
        candidates = self._apply_regime_scaling(candidates, regime, lambda_val)
        
        # Step 5: Markowitz optimization
        allocation = self._markowitz_optimize(candidates, bankroll)

        # Step 5b: Enforce per-market exposure cap so policy check passes
        allocation = self._enforce_market_caps(allocation)

        # Step 6: Build final allocation vector
        vectors = self._build_allocation_vectors(allocation, bankroll)
        
        # Step 7: Create NEW PortfolioState
        new_state = self._create_new_state(
            previous_state=previous_state,
            vectors=vectors,
            bankroll=bankroll,
            regime=regime,
            lambda_val=lambda_val
        )
        
        # Step 8: Emit portfolio event
        self._emit_portfolio_event(vectors, regime, lambda_val)
        
        logger.info(f"[PORTFOLIO] Allocation complete: {len(vectors)} bets")
        
        return vectors, new_state
    
    def _apply_learning_weights(self, candidates: List[dict], state: PortfolioState) -> List[dict]:
        """Apply learning weights from PortfolioState."""
        weights = state.allocation_weights
        
        for c in candidates:
            market = c.get("market", "h2h")
            market_weight = weights.get(market, 0.25)
            c["ev"] = c.get("ev", 0) * market_weight
        
        return candidates
    
    def _create_new_state(
        self,
        previous_state: PortfolioState,
        vectors: List[AllocationVector],
        bankroll: float,
        regime: str,
        lambda_val: float
    ) -> PortfolioState:
        """Create new PortfolioState from allocation results."""
        # Build allocations dict
        allocations = {}
        market_exposure = {}
        
        for v in vectors:
            bet_id = v.bet_id or f"{v.market}_{v.fixture_id}"
            allocations[bet_id] = {
                "stake": v.stake,
                "market": v.market,
                "odds": v.odds,
                "expected_return": v.expected_return,
            }
            market_exposure[v.market] = market_exposure.get(v.market, 0) + v.stake
        
        # Normalize market exposure
        total = sum(market_exposure.values())
        if total > 0:
            market_exposure = {k: v/total for k, v in market_exposure.items()}
        
        # Copy and update state
        new_state = previous_state.copy()
        new_state.allocations = allocations
        new_state.exposure_by_market = market_exposure
        new_state.risk_lambda = lambda_val
        new_state.regime = regime
        new_state.timestamp = datetime.utcnow().isoformat()
        new_state.run_count = previous_state.run_count + 1
        
        return new_state
    
    def _prepare_candidates(self, predictions: List[dict]) -> List[dict]:
        """Prepare candidates for optimization."""
        candidates = []
        
        for i, pred in enumerate(predictions):
            ev = pred.get("ev") or 0.0
            odds = pred.get("odds") or 0.0
            # Skip preliminary predictions (no odds) — they have no betting value yet.
            if not odds or ev <= 0:
                continue
            candidates.append({
                "id": f"pred_{i}_{pred.get('fixture_id')}_{pred.get('market')}",
                "fixture_id": pred.get("fixture_id", 0),
                "market": pred.get("market", "h2h"),
                "outcome": pred.get("outcome", ""),
                "odds": odds,
                "our_prob": pred.get("our_prob", 0.5),
                "ev": ev,
                "kelly": pred.get("kelly_fraction", pred.get("kelly", 0)),
                "correlation_key": f"fixture_{pred.get('fixture_id')}",
            })
        
        return candidates
    
    def _apply_adaptive_weights(self, candidates: List[dict]) -> List[dict]:
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
        """Run Markowitz optimization with CVXPY fallback."""
        use_cvxpy = self.config.use_cvxpy and is_cvxpy_available()
        
        if use_cvxpy and candidates:
            try:
                returns = np.array([c.get("ev", 0) for c in candidates])
                
                n = len(candidates)
                
                try:
                    markets = [c.get("market", "h2h") for c in candidates]
                    cov_matrix = self._correlation.get_covariance_matrix(markets)
                except AttributeError:
                    cov_matrix = np.eye(n) * 0.01
                
                if cov_matrix.shape[0] != n:
                    cov_matrix = np.eye(n) * 0.01
                
                lambda_val = self.config.risk_aversion
                weights, status = self._cvxpy_optimizer.optimize(
                    returns, cov_matrix, risk_aversion=lambda_val
                )
                
                self._optimizer_backend = "cvxpy"
                self._optimization_status = status
                
                if status != "optimal":
                    self._last_solver_error = f"status: {status}"
                    logger.warning(f"[PORTFOLIO] CVXPY status: {status}, using fallback")
                    return self._heuristic_optimize(candidates, bankroll)
                
                if len(weights) != len(candidates) or np.any(np.isnan(weights)):
                    logger.warning("[PORTFOLIO] CVXPY returned invalid weights, using fallback")
                    return self._heuristic_optimize(candidates, bankroll)
                
                allocations = []
                for i, (c, w) in enumerate(zip(candidates, weights)):
                    stake = w * bankroll
                    if stake >= self.config.min_bet:
                        allocations.append({
                            "bet_id": c.get("id", f"bet_{i}"),
                            "fixture_id": c.get("fixture_id", 0),
                            "market": c.get("market", ""),
                            "outcome": c.get("outcome", ""),
                            "odds": c.get("odds", 0),
                            "stake": stake,
                            "expected_return": c.get("ev", 0),
                            "risk_contribution": 0,
                            "weight": w,
                            "ev": c.get("ev", 0),
                            "our_prob": c.get("our_prob", 0.5),
                        })
                
                if not allocations:
                    logger.warning("[PORTFOLIO] CVXPY produced no allocations, using fallback")
                    return self._heuristic_optimize(candidates, bankroll)
                
                logger.info(f"[PORTFOLIO] CVXPY optimization: {status}, {len(allocations)} allocations")
                return allocations
                
            except Exception as e:
                self._optimizer_backend = "cvxpy_fallback"
                self._optimization_status = "error"
                self._last_solver_error = str(e)
                logger.warning(f"[PORTFOLIO] CVXPY failed: {e}, falling back to heuristic")
        
        self._optimizer_backend = "heuristic"
        self._optimization_status = "heuristic_fallback"
        
        return self._heuristic_optimize(candidates, bankroll)
    
    def _heuristic_optimize(
        self,
        candidates: List[dict],
        bankroll: float
    ) -> List[dict]:
        """Heuristic optimization fallback."""
        self._markowitz.config.risk_aversion = self.config.risk_aversion
        self._markowitz.config.max_bet_pct = self.config.max_per_bet
        self._markowitz.config.max_total_exposure = self.config.max_total_exposure
        self._markowitz.config.min_bet = self.config.min_bet
        
        result = self._markowitz.optimize(candidates, bankroll)
        
        return [
            {
                "bet_id": b.bet_id,
                "fixture_id": b.fixture_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "expected_return": b.expected_return,
                "risk_contribution": b.risk_contribution,
                "ev": b.ev,
                "our_prob": b.our_prob,
            }
            for b in result.bets
        ]
    
    def _enforce_market_caps(
        self,
        allocation: List[dict],
        max_market_fraction: float = 0.60
    ) -> List[dict]:
        """Scale down bets in any market that exceeds max_market_fraction of total stake."""
        if not allocation:
            return allocation
        total_stake = sum(a.get("stake", 0) for a in allocation)
        if total_stake <= 0:
            return allocation
        market_stake = {}
        for a in allocation:
            m = a.get("market", "")
            market_stake[m] = market_stake.get(m, 0) + a.get("stake", 0)
        scaling = {
            m: max_market_fraction / (stake / total_stake)
            for m, stake in market_stake.items()
            if (stake / total_stake) > max_market_fraction
        }
        if not scaling:
            return allocation
        logger.info(f"[PORTFOLIO] Market cap applied — scaling: { {m: f'{s:.2f}x' for m, s in scaling.items()} }")
        return [
            {**a, "stake": a["stake"] * scaling[a.get("market", "")]}
            if a.get("market", "") in scaling else a
            for a in allocation
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
                ev=a.get("ev", 0),
                our_prob=a.get("our_prob", 0.5),
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
            "source_chain": [
                "PortfolioEngine",
                "RiskEngine",
                "MonteCarlo",
                "PolicyEngine",
                "ExecutionEngine"
            ],
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
