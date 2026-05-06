"""
Meta-Policy Learning Layer - Learning whether the rules were correct.

This system dynamically adjusts risk policy itself based on long-term 
performance outcomes. It is NOT prediction or portfolio optimization.

It learns whether the PolicyEngine constraints were correct and
adjusts them within safe boundaries.

CORE CONCEPT:
- PortfolioEngine → chooses allocations
- RiskEngine → computes λ + regime
- PolicyEngine → enforces constraints
- MetaPolicyEngine → tunes PolicyEngine itself

INPUT DATA (LONG HORIZON ONLY):
- 30-180 day rolling windows
- Full PortfolioState histories
- PolicyEngine decisions + outcomes
- Drawdowns after approvals
- Rejected-but-would-have-won scenarios (counterfactual simulation)
"""

import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import numpy as np

from src.alerts.event_bus import event_bus, Events
from src.governance.policy_engine import PolicyEngine, PolicyDecision, PolicyConstraint

logger = logging.getLogger(__name__)


@dataclass
class PolicyOutcome:
    """Outcome of a policy decision."""
    decision_id: str
    decision: str  # "approve", "throttle", "reject"
    policy_decision_type: str  # from PolicyDecisionType
    constraint_violations: list[str] = field(default_factory=list)
    
    # After execution
    approved: bool = False
    resulted_in_drawdown: bool = False
    drawdown_magnitude: float = 0.0
    resulted_in_profit: bool = False
    profit_magnitude: float = 0.0
    
    # Counterfactual (for rejected)
    simulated_outcome: Optional[float] = None
    
    # Timing
    decided_at: str = ""
    settled_at: str = ""


@dataclass
class ConstraintParameter:
    """Learnable parameter for a constraint."""
    name: str
    current_value: float
    min_value: float
    max_value: float
    learning_rate: float = 0.05
    stability_score: float = 1.0


@dataclass
class PolicyUpdate:
    """Result of meta-policy learning."""
    adjusted_constraints: dict[str, float] = field(default_factory=dict)
    confidence_score: float = 0.0
    stability_flag: str = "stable"  # "stable", "warning", "unstable"
    changes_log: list[dict] = field(default_factory=list)
    timestamp: str = ""


class MetaPolicyEngine:
    """
    Meta-Policy Learning Layer.
    
    Operates on 30-180 day rolling windows to learn whether
    PolicyEngine constraints were correct.
    
    SAFETY BOUNDARIES:
    - Cannot increase ruin probability threshold above fixed cap
    - Cannot disable kill-switch constraints
    - Cannot remove drawdown limit entirely
    
    It can ONLY shift parameters inside safe envelope.
    """
    
    def __init__(self, history_dir: str = "/opt/projects/bootball/data/meta_policy"):
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self._policy_outcomes: list[PolicyOutcome] = []
        
        # Initialize constraint parameters with safe bounds
        self._constraint_params: dict[str, ConstraintParameter] = {
            "max_drawdown": ConstraintParameter(
                name="max_drawdown",
                current_value=0.15,
                min_value=0.10,
                max_value=0.25,
                learning_rate=0.03,
            ),
            "max_ruin_prob": ConstraintParameter(
                name="max_ruin_prob",
                current_value=0.02,
                min_value=0.01,
                max_value=0.05,  # Cap - cannot exceed this
                learning_rate=0.02,
            ),
            "max_exposure": ConstraintParameter(
                name="max_exposure",
                current_value=0.35,
                min_value=0.20,
                max_value=0.50,
                learning_rate=0.05,
            ),
            "max_volatility": ConstraintParameter(
                name="max_volatility",
                current_value=0.15,
                min_value=0.10,
                max_value=0.25,
                learning_rate=0.03,
            ),
            "regime_bull_threshold": ConstraintParameter(
                name="regime_bull_threshold",
                current_value=0.02,
                min_value=0.01,
                max_value=0.05,
                learning_rate=0.04,
            ),
            "regime_defensive_threshold": ConstraintParameter(
                name="regime_defensive_threshold",
                current_value=0.10,
                min_value=0.05,
                max_value=0.20,
                learning_rate=0.04,
            ),
        }
        
        # Meta-learning windows
        self.short_window_days = 30
        self.medium_window_days = 90
        self.long_window_days = 180
        
        # Stability tracking
        self._stability_history: list[float] = []
        self._overfitting_count = 0
        
        logger.info("[META_POLICY] Meta-Policy Engine initialized")
    
    def add_policy_outcome(self, outcome: PolicyOutcome) -> None:
        """Add a policy outcome for meta-learning."""
        self._policy_outcomes.append(outcome)
        
        # Keep only relevant history
        cutoff = datetime.utcnow() - timedelta(days=self.long_window_days)
        self._policy_outcomes = [
            o for o in self._policy_outcomes
            if datetime.fromisoformat(o.decided_at) > cutoff
        ]
        
        logger.debug(f"[META_POLICY] Added outcome: {outcome.decision_id}")
    
    def update_policy(self, history: list[PolicyOutcome] = None) -> PolicyUpdate:
        """
        Update policy based on long-term learning.
        
        This should be called periodically (e.g., weekly) not on every run.
        
        Args:
            history: Optional override for policy outcomes
            
        Returns:
            PolicyUpdate with adjusted constraints
        """
        history = history or self._policy_outcomes
        
        if len(history) < 20:
            logger.info("[META_POLICY] Insufficient history for meta-learning")
            return PolicyUpdate(timestamp=datetime.utcnow().isoformat())
        
        logger.info(f"[META_POLICY] Updating policy from {len(history)} outcomes")
        
        update = PolicyUpdate(timestamp=datetime.utcnow().isoformat())
        
        # Analyze good vs bad signals
        good_signals, bad_signals = self._analyze_signals(history)
        
        # Update each constraint parameter
        for param_name, param in self._constraint_params.items():
            adjustment = self._compute_adjustment(
                param_name=param_name,
                good_signals=good_signals,
                bad_signals=bad_signals,
                history=history
            )
            
            if adjustment != 0:
                old_value = param.current_value
                new_value = param.current_value + adjustment
                
                # Apply safety boundaries
                new_value = max(param.min_value, min(param.max_value, new_value))
                
                # Update stability score
                param.stability_score = self._compute_stability(param_name)
                
                update.adjusted_constraints[param_name] = new_value
                update.changes_log.append({
                    "parameter": param_name,
                    "old_value": old_value,
                    "new_value": new_value,
                    "adjustment": adjustment,
                    "stability": param.stability_score,
                })
                
                logger.info(f"[META_POLICY] {param_name}: {old_value:.3f} -> {new_value:.3f}")
        
        # Compute confidence and stability
        update.confidence_score = self._compute_confidence(history)
        update.stability_flag = self._compute_stability_flag()
        
        # Detect overfitting
        if self._detect_overfitting(update):
            update.stability_flag = "unstable"
            self._overfitting_count += 1
            
            event_bus.emit(Events.POLICY_OVERFITTING_DETECTED, {
                "stability_flag": update.stability_flag,
                "overfitting_count": self._overfitting_count,
                "timestamp": update.timestamp,
            })
        
        # Emit events
        self._emit_meta_policy_events(update)
        
        # Save history
        self._save_history()
        
        logger.info(f"[META_POLICY] Update complete: confidence={update.confidence_score:.2f}, "
                   f"stability={update.stability_flag}")
        
        return update
    
    def _analyze_signals(self, history: list[PolicyOutcome]) -> tuple[list, list]:
        """Analyze good vs bad signals from history."""
        good_signals = []
        bad_signals = []
        
        for outcome in history:
            # GOOD: approved + profitable + low drawdown
            if outcome.approved and outcome.resulted_in_profit and not outcome.resulted_in_drawdown:
                good_signals.append({
                    "type": "approved_profitable",
                    "magnitude": outcome.profit_magnitude,
                    "drawdown": outcome.drawdown_magnitude,
                })
            
            # GOOD: approved + stable (low volatility)
            elif outcome.approved and outcome.drawdown_magnitude < 0.02:
                good_signals.append({
                    "type": "stable_growth",
                    "drawdown": outcome.drawdown_magnitude,
                })
            
            # BAD: approved but caused drawdown
            elif outcome.approved and outcome.resulted_in_drawdown:
                bad_signals.append({
                    "type": "drawdown_approved",
                    "magnitude": outcome.drawdown_magnitude,
                })
            
            # BAD: rejected but would have won (counterfactual)
            elif not outcome.approved and outcome.simulated_outcome is not None:
                if outcome.simulated_outcome > 0:
                    bad_signals.append({
                        "type": "rejected_winner",
                        "magnitude": outcome.simulated_outcome,
                    })
        
        return good_signals, bad_signals
    
    def _compute_adjustment(
        self,
        param_name: str,
        good_signals: list,
        bad_signals: list,
        history: list[PolicyOutcome]
    ) -> float:
        """Compute parameter adjustment based on signals."""
        param = self._constraint_params[param_name]
        
        # Count signals in different windows
        recent_30 = [o for o in history if 
                    datetime.fromisoformat(o.decided_at) > 
                    datetime.utcnow() - timedelta(days=30)]
        
        recent_90 = [o for o in history if 
                    datetime.fromisoformat(o.decided_at) > 
                    datetime.utcnow() - timedelta(days=90)]
        
        if param_name == "max_drawdown":
            # If approved strategies cause drawdowns, INCREASE drawdown limit (be more conservative)
            bad_dd = sum(s["drawdown"] for s in bad_signals if s.get("type") == "drawdown_approved")
            good_dd = sum(s["drawdown"] for s in good_signals)
            
            if len(recent_30) > 5 and bad_dd > good_dd * 2:
                return param.learning_rate  # More conservative
            elif len(recent_30) > 5 and bad_dd == 0:
                return -param.learning_rate * 0.5  # Slightly more aggressive
        
        elif param_name == "max_exposure":
            # If approved portfolios are stable, can increase exposure
            stable_count = len([s for s in good_signals if s.get("type") == "stable_growth"])
            if stable_count > 10:
                return param.learning_rate
            elif len(bad_signals) > 5:
                return -param.learning_rate
        
        elif param_name == "max_ruin_prob":
            # NEVER increase ruin probability - this is safety critical
            # Can only decrease (more conservative)
            return 0  # No learning for kill-switch params
        
        elif param_name == "max_volatility":
            # If volatility is causing issues, increase limit
            if len(bad_signals) > len(good_signals):
                return param.learning_rate
            elif len(good_signals) > len(bad_signals) * 2:
                return -param.learning_rate
        
        return 0.0
    
    def _compute_stability(self, param_name: str) -> float:
        """Compute stability score for a parameter."""
        # Simple stability: inverse of adjustment variance
        if len(self._stability_history) < 5:
            return 1.0
        
        recent = self._stability_history[-20:]
        if not recent:
            return 1.0
        
        variance = np.var(recent)
        return max(0, 1.0 - variance * 10)
    
    def _compute_confidence(self, history: list[PolicyOutcome]) -> float:
        """Compute confidence score based on history quality."""
        if len(history) < 30:
            return 0.3
        elif len(history) < 60:
            return 0.6
        elif len(history) < 120:
            return 0.8
        else:
            return 0.9
    
    def _compute_stability_flag(self) -> str:
        """Compute stability flag."""
        if len(self._stability_history) < 3:
            return "stable"
        
        recent = self._stability_history[-10:]
        avg_stability = np.mean(recent)
        
        if avg_stability > 0.8:
            return "stable"
        elif avg_stability > 0.5:
            return "warning"
        else:
            return "unstable"
    
    def _detect_overfitting(self, update: PolicyUpdate) -> bool:
        """Detect if policy is overfitting (too many changes)."""
        if len(update.changes_log) > 3:
            return True
        
        total_adjustment = sum(abs(c.get("adjustment", 0)) for c in update.changes_log)
        if total_adjustment > 0.15:
            return True
        
        return False
    
    def _emit_meta_policy_events(self, update: PolicyUpdate) -> None:
        """Emit meta-policy events."""
        if not update.changes_log:
            return
        
        # Emit adjustment event
        event_bus.emit(Events.META_POLICY_ADJUSTED, {
            "adjusted_constraints": update.adjusted_constraints,
            "confidence_score": update.confidence_score,
            "stability_flag": update.stability_flag,
            "timestamp": update.timestamp,
        })
        
        # Detect risk appetite changes
        param = self._constraint_params.get("max_drawdown")
        if param:
            if param.current_value > 0.18:
                event_bus.emit(Events.RISK_APPETITE_INCREASED, {
                    "max_drawdown": param.current_value,
                    "timestamp": update.timestamp,
                })
            elif param.current_value < 0.12:
                event_bus.emit(Events.RISK_APPETITE_REDUCED, {
                    "max_drawdown": param.current_value,
                    "timestamp": update.timestamp,
                })
    
    def get_current_parameters(self) -> dict[str, float]:
        """Get current constraint parameters."""
        return {k: v.current_value for k, v in self._constraint_params.items()}
    
    def get_parameter(self, name: str) -> Optional[ConstraintParameter]:
        """Get a specific parameter."""
        return self._constraint_params.get(name)
    
    def _save_history(self) -> None:
        """Save policy outcome history."""
        filepath = self.history_dir / "policy_outcomes.jsonl"
        
        with open(filepath, 'w') as f:
            for outcome in self._policy_outcomes:
                f.write(json.dumps({
                    "decision_id": outcome.decision_id,
                    "decision": outcome.decision,
                    "policy_decision_type": outcome.policy_decision_type,
                    "approved": outcome.approved,
                    "resulted_in_drawdown": outcome.resulted_in_drawdown,
                    "drawdown_magnitude": outcome.drawdown_magnitude,
                    "resulted_in_profit": outcome.resulted_in_profit,
                    "profit_magnitude": outcome.profit_magnitude,
                    "simulated_outcome": outcome.simulated_outcome,
                    "decided_at": outcome.decided_at,
                    "settled_at": outcome.settled_at,
                }) + "\n")
    
    def load_history(self) -> None:
        """Load policy outcome history."""
        filepath = self.history_dir / "policy_outcomes.jsonl"
        
        if not filepath.exists():
            return
        
        with open(filepath, 'r') as f:
            for line in f:
                data = json.loads(line)
                outcome = PolicyOutcome(
                    decision_id=data.get("decision_id", ""),
                    decision=data.get("decision", ""),
                    policy_decision_type=data.get("policy_decision_type", ""),
                    approved=data.get("approved", False),
                    resulted_in_drawdown=data.get("resulted_in_drawdown", False),
                    drawdown_magnitude=data.get("drawdown_magnitude", 0.0),
                    resulted_in_profit=data.get("resulted_in_profit", False),
                    profit_magnitude=data.get("profit_magnitude", 0.0),
                    simulated_outcome=data.get("simulated_outcome"),
                    decided_at=data.get("decided_at", ""),
                    settled_at=data.get("settled_at", ""),
                )
                self._policy_outcomes.append(outcome)
        
        logger.info(f"[META_POLICY] Loaded {len(self._policy_outcomes)} outcomes")


# Global instance
_engine: Optional[MetaPolicyEngine] = None


def get_meta_policy_engine() -> MetaPolicyEngine:
    """Get global meta policy engine."""
    global _engine
    if _engine is None:
        _engine = MetaPolicyEngine()
        _engine.load_history()
    return _engine
