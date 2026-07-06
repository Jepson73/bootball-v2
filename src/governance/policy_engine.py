"""
Policy Constraint Engine - HARD CONSTRAINT GOVERNOR.

This layer sits above Monte Carlo + Portfolio Optimization and enforces
hard behavioral rules over simulated and real PortfolioState trajectories.

CORE ROLE:
- Does NOT predict, optimize, or simulate
- Decides what the system is allowed to do
- Filters, blocks, or scales any strategy that violates system risk policy
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

import numpy as np

from src.events.event_bus import event_bus, Events
from src.portfolio.state.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Constraint severity levels."""
    SOFT = "soft"       # Penalize but don't block
    HARD = "hard"       # Block if violated
    KILL_SWITCH = "kill-switch"  # Override all systems


class PolicyDecisionType(Enum):
    """Policy decision outcomes."""
    APPROVE = "approve"      # Strategy passes all hard constraints
    THROTTLE = "throttle"    # Reduce allocation size due to elevated risk
    REJECT = "reject"        # Violates hard constraint or kill-switch


@dataclass
class MonteCarloResults:
    """Monte Carlo simulation results."""
    trajectories: list[list[PortfolioState]] = field(default_factory=list)
    final_balances: list[float] = field(default_factory=list)
    max_drawdowns: list[float] = field(default_factory=list)
    ruin_count: int = 0
    mean_return: float = 0.0
    volatility: float = 0.0
    percentile_5: float = 0.0
    percentile_95: float = 0.0


@dataclass
class PolicyConstraint:
    """
    Base class for policy constraints.
    
    Each constraint defines a rule over PortfolioState trajectories.
    """
    name: str
    severity: Severity
    description: str = ""
    
    @abstractmethod
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        """
        Evaluate constraint on trajectory.
        
        Args:
            trajectory: List of PortfolioState representing simulation path
            current_state: Current real-time state
            
        Returns:
            Tuple of (passed: bool, risk_score: float)
        """
        pass
    
    def _calculate_risk_score(self, value: float, threshold: float) -> float:
        """Calculate risk score based on how close to threshold."""
        if value <= threshold:
            return 0.0
        return min(1.0, (value - threshold) / threshold)


@dataclass
class DrawdownConstraint(PolicyConstraint):
    """Constraint: Maximum drawdown allowed."""
    
    max_drawdown: float = 0.15  # 15% default
    
    def __init__(self, max_drawdown: float = 0.15):
        super().__init__(
            name="drawdown_constraint",
            severity=Severity.HARD,
            description=f"Max drawdown: {max_drawdown:.0%}"
        )
        self.max_drawdown = max_drawdown
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if not trajectory:
            return True, 0.0
        
        # Check max drawdown in trajectory
        max_dd = max((s.drawdown for s in trajectory), default=0.0)
        
        passed = max_dd <= self.max_drawdown
        risk_score = self._calculate_risk_score(max_dd, self.max_drawdown)
        
        logger.info(f"[POLICY] Drawdown: {max_dd:.2%} vs limit {self.max_drawdown:.2%} = {'PASS' if passed else 'FAIL'}")
        
        return passed, risk_score


@dataclass
class RuinProbabilityConstraint(PolicyConstraint):
    """Constraint: Maximum ruin probability (kill-switch)."""
    
    max_ruin_prob: float = 0.02  # 2% default
    ruin_threshold: float = 100.0  # SEK bankroll considered ruin
    
    def __init__(self, max_ruin_prob: float = 0.02, ruin_threshold: float = 100.0):
        super().__init__(
            name="ruin_probability_constraint",
            severity=Severity.KILL_SWITCH,
            description=f"Max ruin probability: {max_ruin_prob:.1%}"
        )
        self.max_ruin_prob = max_ruin_prob
        self.ruin_threshold = ruin_threshold
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if not trajectory:
            return True, 0.0
        
        # If no bets have been placed (first state has realized_pnl=0), ruin probability is 0
        if hasattr(trajectory[0], 'realized_pnl') and trajectory[0].realized_pnl == 0:
            logger.info(f"[POLICY] Ruin probability: 0.00% (no bets placed yet)")
            return True, 0.0
        
        # Calculate ruin probability from final balances
        # Use current balance if available, otherwise use realized_pnl
        final_balances = []
        for s in trajectory:
            if hasattr(s, 'balance') and s.balance is not None:
                final_balances.append(s.balance)
            else:
                final_balances.append(s.realized_pnl if hasattr(s, 'realized_pnl') else 0)
        
        if not final_balances:
            return True, 0.0
        
        ruin_count = sum(1 for b in final_balances if b < self.ruin_threshold)
        ruin_prob = ruin_count / len(final_balances) if final_balances else 0.0
        
        passed = ruin_prob <= self.max_ruin_prob
        risk_score = self._calculate_risk_score(ruin_prob, self.max_ruin_prob)
        
        logger.info(f"[POLICY] Ruin probability: {ruin_prob:.2%} vs limit {self.max_ruin_prob:.2%} = {'PASS' if passed else 'FAIL'}")
        
        return passed, risk_score


@dataclass
class VolatilityConstraint(PolicyConstraint):
    """Constraint: Maximum volatility (soft - penalize but don't block)."""
    
    max_volatility: float = 0.15  # 15% daily volatility default
    
    def __init__(self, max_volatility: float = 0.15):
        super().__init__(
            name="volatility_constraint",
            severity=Severity.SOFT,
            description=f"Max volatility: {max_volatility:.0%}"
        )
        self.max_volatility = max_volatility
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if not trajectory:
            return True, 0.0
        
        # Calculate volatility from historical ROI
        if len(trajectory) < 2:
            return True, 0.0
        
        roi_values = trajectory[-20:]  # Last 20 states
        roi_array = np.array([s.roi for s in roi_values])
        
        if len(roi_array) < 2:
            return True, 0.0
        
        volatility = float(np.std(roi_array))
        
        passed = volatility <= self.max_volatility
        risk_score = self._calculate_risk_score(volatility, self.max_volatility)
        
        logger.info(f"[POLICY] Volatility: {volatility:.2%} vs limit {self.max_volatility:.2%} = {'PASS' if passed else 'FAIL'}")
        
        return passed, risk_score


@dataclass
class ExposureConcentrationConstraint(PolicyConstraint):
    """Constraint: Maximum single market exposure."""
    
    max_market_exposure: float = 0.35  # 35% default
    
    def __init__(self, max_market_exposure: float = 0.35):
        super().__init__(
            name="exposure_concentration_constraint",
            severity=Severity.SOFT,
            description=f"Max market exposure: {max_market_exposure:.0%}"
        )
        self.max_market_exposure = max_market_exposure
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if current_state is None:
            return True, 0.0
        
        # Check current exposure by market
        if not current_state.exposure_by_market:
            return True, 0.0
        
        max_exposure = max(current_state.exposure_by_market.values(), default=0.0)
        
        passed = max_exposure <= self.max_market_exposure
        risk_score = self._calculate_risk_score(max_exposure, self.max_market_exposure)
        
        logger.info(f"[POLICY] Max market exposure: {max_exposure:.0%} vs limit {self.max_market_exposure:.0%} = {'PASS' if passed else 'FAIL'}")
        
        return passed, risk_score


@dataclass
class CorrelationClusterConstraint(PolicyConstraint):
    """Constraint: Prevent overexposure to correlated bet clusters."""
    
    # Correlation pairs that shouldn't exceed threshold
    correlation_pairs: dict = field(default_factory=lambda: {
        ("btts", "ou25"): 0.65,
        ("ou25", "ou15"): 0.70,
        ("h2h", "btts"): 0.20,
    })
    max_correlation: float = 0.70
    
    def __init__(self):
        super().__init__(
            name="correlation_cluster_constraint",
            severity=Severity.SOFT,  # Penalise but don't block; h2h+btts both >50% is genuinely risky
            description="Warn on correlated bet clusters"
        )
        self.correlation_pairs = {
            ("btts", "ou25"): 0.65,
            ("ou25", "ou15"): 0.70,
            ("h2h", "btts"): 0.50,
        }
        self.max_correlation = 0.70
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if current_state is None or not current_state.exposure_by_market:
            return True, 0.0
        
        # Check correlation pairs
        exposure = current_state.exposure_by_market
        violations = []
        
        for (m1, m2), max_corr in self.correlation_pairs.items():
            e1 = exposure.get(m1, 0)
            e2 = exposure.get(m2, 0)

            # Higher correlation → lower tolerance: threshold = 1 - max_corr
            # e.g. 0.70 corr → both must exceed 0.30; 0.50 corr → both must exceed 0.50
            threshold = 1 - max_corr
            if e1 > threshold and e2 > threshold:
                violations.append(f"{m1}+{m2}")
        
        risk_score = len(violations) / 3.0 if violations else 0.0
        passed = len(violations) == 0
        
        if violations:
            logger.warning(f"[POLICY] Correlation cluster violations: {violations}")
        
        return passed, risk_score


@dataclass
class RegimeCompatibilityConstraint(PolicyConstraint):
    """Constraint: Regime-based strategy compatibility."""
    
    def __init__(self):
        super().__init__(
            name="regime_compatibility_constraint",
            severity=Severity.SOFT,
            description="Regime-based strategy compatibility"
        )
        self.strategy_multipliers = {
            "defensive": 0.5,   # Reduce aggressive strategies
            "neutral": 1.0,
            "bull": 1.2,        # Allow more aggression
        }
    
    def evaluate(self, trajectory: list[PortfolioState], current_state: PortfolioState = None) -> tuple[bool, float]:
        if current_state is None:
            return True, 0.0
        
        regime = current_state.regime
        
        # In defensive mode, this becomes HARD
        if regime == "defensive":
            self.severity = Severity.HARD
        else:
            self.severity = Severity.SOFT
        
        # For now, always pass but calculate risk score
        risk_score = 1.0 - self.strategy_multipliers.get(regime, 1.0)
        
        logger.info(f"[POLICY] Regime: {regime}, severity: {self.severity.value}")
        
        return True, max(0, risk_score)


@dataclass
class PolicyDecision:
    """Policy engine decision output."""
    decision: PolicyDecisionType
    approved: bool
    risk_score: float
    violated_constraints: list[str] = field(default_factory=list)
    adjusted_allocation_scale: float = 1.0
    throttle_reason: str = ""
    reject_reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class PolicyEngine:
    """
    HARD CONSTRAINT GOVERNOR.
    
    Evaluates Monte Carlo simulation results against policy constraints
    and returns a PolicyDecision.
    
    Flow:
    PortfolioEngine → RiskEngine → MonteCarlo → PolicyEngine → ExecutionEngine
    """
    
    def __init__(self):
        self.constraints: list[PolicyConstraint] = []
        self._initialize_constraints()
        
        logger.info("[POLICY] Policy Engine initialized with constraints")
    
    def _initialize_constraints(self):
        """Initialize all policy constraints."""
        self.constraints = [
            DrawdownConstraint(max_drawdown=0.15),
            RuinProbabilityConstraint(max_ruin_prob=0.02),
            VolatilityConstraint(max_volatility=0.15),
            ExposureConcentrationConstraint(max_market_exposure=0.75),
            CorrelationClusterConstraint(),
            RegimeCompatibilityConstraint(),
        ]
    
    def add_constraint(self, constraint: PolicyConstraint):
        """Add a custom constraint."""
        self.constraints.append(constraint)
        logger.info(f"[POLICY] Added constraint: {constraint.name}")
    
    def remove_constraint(self, name: str):
        """Remove a constraint by name."""
        self.constraints = [c for c in self.constraints if c.name != name]
        logger.info(f"[POLICY] Removed constraint: {name}")
    
    def evaluate(
        self,
        simulation_results: MonteCarloResults,
        current_state: PortfolioState = None,
        proposed_allocation: dict = None
    ) -> PolicyDecision:
        """
        Evaluate simulation results against all constraints.
        
        Args:
            simulation_results: Monte Carlo simulation results
            current_state: Current real-time PortfolioState
            proposed_allocation: Proposed allocation dictionary
            
        Returns:
            PolicyDecision with approval, risk score, and constraints
        """
        logger.info("[POLICY] Evaluating policy constraints")
        
        violated_constraints = []
        soft_violations = []
        total_risk_score = 0.0
        
        # Flatten all trajectories for constraint evaluation
        all_states = []
        for traj in simulation_results.trajectories:
            all_states.extend(traj)
        
        # Add current state if provided
        if current_state:
            all_states.append(current_state)
        
        # Evaluate each constraint
        for constraint in self.constraints:
            passed, risk_score = constraint.evaluate(all_states, current_state)
            
            total_risk_score = max(total_risk_score, risk_score)
            
            if not passed:
                violated_constraints.append(constraint.name)
                
                if constraint.severity == Severity.SOFT:
                    soft_violations.append(constraint.name)
                    
                    logger.warning(f"[POLICY] SOFT violation: {constraint.name} (risk: {risk_score:.2f})")
                elif constraint.severity == Severity.HARD:
                    logger.warning(f"[POLICY] HARD violation: {constraint.name} (risk: {risk_score:.2f})")
                elif constraint.severity == Severity.KILL_SWITCH:
                    logger.critical(f"[POLICY] KILL-SWITCH violation: {constraint.name}")
        
        # Determine decision
        decision = self._make_decision(
            violated_constraints=violated_constraints,
            soft_violations=soft_violations,
            total_risk_score=total_risk_score,
            simulation_results=simulation_results
        )
        
        # Emit events
        self._emit_policy_events(decision, violated_constraints)
        
        logger.info(f"[POLICY] Decision: {decision.decision.value}, risk: {total_risk_score:.2f}")
        
        return decision
    
    def _make_decision(
        self,
        violated_constraints: list[str],
        soft_violations: list[str],
        total_risk_score: float,
        simulation_results: MonteCarloResults
    ) -> PolicyDecision:
        """Make policy decision based on violations."""
        
        # Check for kill-switch violations
        kill_switch_constraints = [
            c.name for c in self.constraints 
            if c.severity == Severity.KILL_SWITCH
        ]
        
        if any(v in kill_switch_constraints for v in violated_constraints):
            return PolicyDecision(
                decision=PolicyDecisionType.REJECT,
                approved=False,
                risk_score=total_risk_score,
                violated_constraints=violated_constraints,
                adjusted_allocation_scale=0.0,
                reject_reason="Kill-switch constraint violated"
            )
        
        # Check for hard violations
        hard_constraints = [
            c.name for c in self.constraints 
            if c.severity == Severity.HARD
        ]
        
        hard_violations = [v for v in violated_constraints if v in hard_constraints]
        
        if hard_violations:
            return PolicyDecision(
                decision=PolicyDecisionType.REJECT,
                approved=False,
                risk_score=total_risk_score,
                violated_constraints=violated_constraints,
                adjusted_allocation_scale=0.0,
                reject_reason=f"Hard constraint violated: {', '.join(hard_violations)}"
            )
        
        # Check for soft violations - throttle
        if soft_violations:
            # Calculate throttle scale based on risk score
            scale = max(0.3, 1.0 - (total_risk_score * 0.5))
            
            return PolicyDecision(
                decision=PolicyDecisionType.THROTTLE,
                approved=True,
                risk_score=total_risk_score,
                violated_constraints=violated_constraints,
                adjusted_allocation_scale=scale,
                throttle_reason=f"Soft constraints triggered: {', '.join(soft_violations)}"
            )
        
        # All constraints passed - APPROVE
        return PolicyDecision(
            decision=PolicyDecisionType.APPROVE,
            approved=True,
            risk_score=total_risk_score,
            violated_constraints=[],
            adjusted_allocation_scale=1.0
        )
    
    def _emit_policy_events(self, decision: PolicyDecision, violations: list[str]):
        """Emit policy events."""
        
        if decision.decision == PolicyDecisionType.APPROVE:
            event_bus.emit(Events.POLICY_APPROVED, {
                "risk_score": decision.risk_score,
                "timestamp": decision.timestamp,
            })
        elif decision.decision == PolicyDecisionType.THROTTLE:
            event_bus.emit(Events.POLICY_THROTTLED, {
                "risk_score": decision.risk_score,
                "violated_constraints": violations,
                "scale": decision.adjusted_allocation_scale,
                "timestamp": decision.timestamp,
            })
        elif decision.decision == PolicyDecisionType.REJECT:
            event_bus.emit(Events.POLICY_REJECTED, {
                "risk_score": decision.risk_score,
                "violated_constraints": violations,
                "reject_reason": decision.reject_reason,
                "timestamp": decision.timestamp,
            })
        
        # Always emit risk limit event if there are violations
        if violations:
            event_bus.emit(Events.RISK_LIMIT_BREACHED, {
                "violated_constraints": violations,
                "risk_score": decision.risk_score,
                "timestamp": decision.timestamp,
            })
        
        # Check for kill-switch
        if decision.decision == PolicyDecisionType.REJECT and "ruin_probability_constraint" in violations:
            event_bus.emit(Events.KILL_SWITCH_TRIGGERED, {
                "reject_reason": decision.reject_reason,
                "timestamp": decision.timestamp,
            })
    
    def apply_decision(self, allocation: list[dict], decision: PolicyDecision) -> list[dict]:
        """
        Apply policy decision to allocation.
        
        Args:
            allocation: List of allocation dicts
            decision: PolicyDecision
            
        Returns:
            Adjusted allocation with scaled stakes
        """
        if not decision.approved:
            logger.warning("[POLICY] Allocation rejected - no bets will be placed")
            return []
        
        if decision.adjusted_allocation_scale < 1.0:
            scale = decision.adjusted_allocation_scale
            logger.info(f"[POLICY] Scaling allocation by {scale:.2f}x")
            
            for bet in allocation:
                bet["stake"] = bet.get("stake", 0) * scale
                bet["expected_return"] = bet.get("expected_return", 0) * scale
                bet["policy_scaled"] = True
        
        return allocation


# Global instance
_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    """Get global policy engine."""
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine
