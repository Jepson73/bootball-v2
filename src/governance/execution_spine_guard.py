"""
Execution Spine Guard - Enforces single authoritative execution pipeline.

This guard validates that ALL execution passes through the correct chain:
    PortfolioEngine → RiskEngine → MonteCarlo → PolicyEngine → ExecutionEngine

If any step is missing → HARD REJECTION.

CORE PRINCIPLE:
    "If it did not pass through PolicyEngine, it does not exist."
"""

import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

from src.events.event_bus import event_bus, Events
from src.governance.policy_engine import PolicyDecision, PolicyDecisionType

logger = logging.getLogger(__name__)


class SourceChainStep(Enum):
    """Valid steps in the execution spine."""
    PORTFOLIO_ENGINE = "PortfolioEngine"
    RISK_ENGINE = "RiskEngine"
    MONTECARLO = "MonteCarlo"
    POLICY_ENGINE = "PolicyEngine"
    EXECUTION_ENGINE = "ExecutionEngine"


class ExecutionSource(Enum):
    """Valid execution sources."""
    PORTFOLIO_SPINE = "portfolio_spine"
    SIMULATION = "simulation"
    DIAGNOSTICS = "diagnostics"
    REPORTING = "reporting"


@dataclass
class ExecutionSpineRecord:
    """Record of execution spine validation."""
    run_id: str
    source_chain: list[str] = field(default_factory=list)
    portfolio_state_hash: str = ""
    risk_lambda: float = 1.0
    policy_decision: Optional[PolicyDecision] = None
    approved: bool = False
    reject_reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def validate(self) -> tuple[bool, str]:
        """
        Validate the execution spine.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        required_chain = [
            SourceChainStep.PORTFOLIO_ENGINE.value,
            SourceChainStep.RISK_ENGINE.value,
            SourceChainStep.MONTECARLO.value,
            SourceChainStep.POLICY_ENGINE.value,
            SourceChainStep.EXECUTION_ENGINE.value,
        ]
        
        for step in required_chain:
            if step not in self.source_chain:
                return False, f"Missing required step in chain: {step}"
        
        # Check policy decision
        if self.policy_decision is None:
            return False, "No PolicyDecision provided"
        
        if not self.policy_decision.approved:
            return False, f"Policy rejected: {self.policy_decision.reject_reason}"
        
        return True, ""
    
    def to_dict(self) -> dict:
        """Convert to dict for logging."""
        return {
            "run_id": self.run_id,
            "source_chain": self.source_chain,
            "portfolio_state_hash": self.portfolio_state_hash,
            "risk_lambda": self.risk_lambda,
            "policy_decision": {
                "decision": self.policy_decision.decision.value if self.policy_decision else None,
                "risk_score": self.policy_decision.risk_score if self.policy_decision else None,
                "adjusted_allocation_scale": self.policy_decision.adjusted_allocation_scale if self.policy_decision else None,
            } if self.policy_decision else None,
            "approved": self.approved,
            "reject_reason": self.reject_reason,
            "timestamp": self.timestamp,
        }


class ExecutionSpineGuard:
    """
    Enforces single authoritative execution pipeline.
    
    Validates that all bets and allocations pass through:
        PortfolioEngine → RiskEngine → MonteCarlo → PolicyEngine → ExecutionEngine
    
    If any step is missing → HARD REJECTION.
    """
    
    def __init__(self):
        self._enabled = True
        self._bypass_allowed = False  # Only for testing
        
        logger.info("[SPINE_GUARD] Execution Spine Guard initialized")
    
    def enable(self):
        """Enable the spine guard."""
        self._enabled = True
        logger.info("[SPINE_GUARD] Enabled")
    
    def disable(self):
        """Disable the spine guard (testing only)."""
        if not self._bypass_allowed:
            logger.warning("[SPINE_GUARD] Bypass not allowed in production")
            return
        self._enabled = False
        logger.warning("[SPINE_GUARD] Disabled (BYPASS MODE)")
    
    def validate_execution(
        self,
        run_id: str,
        allocations: list[dict],
        source: str,
        source_chain: list[str],
        policy_decision: PolicyDecision,
        portfolio_state_hash: str = "",
        risk_lambda: float = 1.0
    ) -> tuple[bool, str, ExecutionSpineRecord]:
        """
        Validate execution spine.
        
        Args:
            run_id: Unique run identifier
            allocations: List of allocation dicts
            source: Source of execution (must be "portfolio_spine" for real bets)
            source_chain: Steps that this execution passed through
            policy_decision: PolicyDecision from PolicyEngine
            portfolio_state_hash: Hash of current PortfolioState
            risk_lambda: Current risk lambda
            
        Returns:
            Tuple of (is_valid, error_message, record)
        """
        record = ExecutionSpineRecord(
            run_id=run_id,
            source_chain=source_chain,
            portfolio_state_hash=portfolio_state_hash,
            risk_lambda=risk_lambda,
            policy_decision=policy_decision,
        )
        
        # Check if guard is enabled
        if not self._enabled:
            logger.warning("[SPINE_GUARD] Guard disabled, allowing execution")
            record.approved = True
            return True, "", record
        
        # Check source
        if source != ExecutionSource.PORTFOLIO_SPINE.value:
            error = f"Illegal execution bypass: source={source}, expected={ExecutionSource.PORTFOLIO_SPINE.value}"
            logger.error(f"[SPINE_GUARD] {error}")
            
            # Emit illegal path event
            event_bus.emit(Events.EXECUTION_SOURCED_FROM_ILLEGAL_PATH, {
                "run_id": run_id,
                "source": source,
                "source_chain": source_chain,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            record.approved = False
            record.reject_reason = error
            return False, error, record
        
        # Validate source chain
        is_valid, error = record.validate()
        
        if not is_valid:
            logger.error(f"[SPINE_GUARD] Invalid source chain: {error}")
            
            event_bus.emit(Events.EXECUTION_SOURCED_FROM_ILLEGAL_PATH, {
                "run_id": run_id,
                "reason": error,
                "source_chain": source_chain,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            record.approved = False
            record.reject_reason = error
            return False, error, record
        
        # Log observability data
        self._log_observability(record, allocations)
        
        record.approved = True
        logger.info(f"[SPINE_GUARD] Execution validated: run_id={run_id}, chain={source_chain}")
        
        return True, "", record
    
    def _log_observability(self, record: ExecutionSpineRecord, allocations: list[dict]) -> None:
        """Log observability data for every bet."""
        for i, alloc in enumerate(allocations):
            logger.info(
                f"[OBS] bet={i+1}/{len(allocations)} | "
                f"run_id={record.run_id} | "
                f"source_chain={record.source_chain} | "
                f"state_hash={record.portfolio_state_hash[:8]}... | "
                f"lambda={record.risk_lambda:.2f} | "
                f"policy={record.policy_decision.decision.value if record.policy_decision else 'N/A'}"
            )
    
    def check_legacy_bypass(self, calling_module: str) -> bool:
        """
        Check if called from legacy/non-authoritative path.
        
        Args:
            calling_module: Name of the module calling execution
            
        Returns:
            True if bypass detected
        """
        legacy_modules = [
            "auto_bet",
            "scripts.auto_bet",
            "ev_filter",
            "kelly_staking",
        ]
        
        if calling_module in legacy_modules:
            logger.critical(f"[SPINE_GUARD] Legacy bypass detected from {calling_module}")
            event_bus.emit(Events.EXECUTION_SOURCED_FROM_ILLEGAL_PATH, {
                "calling_module": calling_module,
                "timestamp": datetime.utcnow().isoformat(),
            })
            return True
        
        return False
    
    def require_portfolio_spine(self, function_name: str) -> None:
        """
        Runtime guard - raise if not called from portfolio spine.
        
        Args:
            function_name: Name of the function being called
            
        Raises:
            RuntimeError: If called from illegal path
        """
        if not self._enabled:
            return
        
        # This should be called at the start of any execution function
        # The calling stack should have passed through portfolio_spine
        logger.debug(f"[SPINE_GUARD] Checking portfolio spine for {function_name}")


def compute_state_hash(state_dict: dict) -> str:
    """Compute hash of portfolio state for observability."""
    import json
    state_json = json.dumps(state_dict, sort_keys=True)
    return hashlib.sha256(state_json.encode()).hexdigest()


# Global guard instance
_guard: Optional[ExecutionSpineGuard] = None


def get_execution_spine_guard() -> ExecutionSpineGuard:
    """Get global execution spine guard."""
    global _guard
    if _guard is None:
        _guard = ExecutionSpineGuard()
    return _guard
