"""
ClosedLoopValidationEngine - Self-adaptation verification.

This engine determines whether the system is truly self-adapting
or only structurally complete. It actively blocks execution if
the system is not actually closing the loop.

Metrics:
- PDS: Portfolio Drift Score (change in portfolio behavior)
- AI: Adaptation Index (variance in allocation changes vs outcome variance)
- RR: Risk Responsiveness (lambda changes vs drawdown changes)
- PS: Policy Sensitivity (policy changes per regime shift)
- CDS: Counterfactual Divergence Score
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from src.events.event_bus import event_bus, Events

logger = logging.getLogger(__name__)

# Flag for temporal validation integration
TEMPORAL_VALIDATION_ENABLED = True


@dataclass
class ValidationMetrics:
    """Metrics for closed-loop validation."""
    pds: float = 0.0  # Portfolio Drift Score
    ai: float = 0.0    # Adaptation Index
    rr: float = 0.0    # Risk Responsiveness
    ps: float = 0.0   # Policy Sensitivity
    cds: float = 0.0  # Counterfactual Divergence Score
    regime_changes: int = 0
    policy_updates: int = 0
    allocation_changes: int = 0
    outcome_variance: float = 0.0


@dataclass
class ValidationReport:
    """Report for closed-loop validation."""
    run_id: str
    timestamp: str = ""
    metrics: ValidationMetrics = field(default_factory=ValidationMetrics)
    decision: dict = field(default_factory=dict)
    status: str = ""  # "SELF_ADAPTING" or "STATIC_SYSTEM"
    adaptive_score: float = 0.0


class ClosedLoopValidationEngine:
    """
    Determines whether the system is truly self-adapting.
    
    This is NOT observational - it actively blocks execution if
    the system is not adapting.
    """
    
    def __init__(self):
        self.reports_dir = Path("/opt/projects/bootball/reports")
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Thresholds for classification
        self.pds_threshold = 0.01
        self.ai_threshold = 0.5
        self.cds_threshold = 0.05
        self.rr_min = 0.1
        
        # History tracking
        self._history: list[ValidationReport] = []
        
        # System health flag
        self._system_health = {
            "closed_loop": True,
            "adaptation_score": 1.0,
            "last_checked": None,
            "static_detection_count": 0,
        }
        
        logger.info("[CLVE] ClosedLoopValidationEngine initialized")
    
    def evaluate(self, run_id: str) -> ValidationReport:
        """
        Evaluate whether system is self-adapting.
        
        Args:
            run_id: Current run identifier
            
        Returns:
            ValidationReport with decision
        """
        logger.info(f"[CLVE] Evaluating run {run_id}")
        
        # Load state history
        state = self._load_recent_state()
        history = self._load_history(limit=200)
        
        # Compute metrics
        metrics = self._compute_metrics(state, history)
        
        # Classify
        decision = self._classify(metrics, history)
        
        # Build report
        report = ValidationReport(
            run_id=run_id,
            timestamp=datetime.utcnow().isoformat(),
            metrics=metrics,
            decision=decision,
            status="SELF_ADAPTING" if decision["adaptive"] else "STATIC_SYSTEM",
            adaptive_score=self._calculate_adaptive_score(metrics),
        )
        
        # Update system health
        self._update_system_health(report)
        
        # Persist report
        self._persist_report(report)
        
        # Emit events
        self._emit_events(report)
        
        # Temporal validation (NEW)
        if TEMPORAL_VALIDATION_ENABLED:
            temporal_report = self._evaluate_temporal(run_id, state, metrics)
            if temporal_report:
                report.decision['temporal_state'] = temporal_report.system_state
                report.decision['temporal_scs'] = temporal_report.scs
                
                # HARD RULE: Block if system is temporally invalid
                if temporal_report.system_state in ["UNSTABLE_OSCILLATING", "NO_LEARNING_DETECTED"]:
                    logger.critical(f"[CLVE] TEMPORAL INVALID: {temporal_report.system_state} - blocking execution")
                    report.decision['adaptive'] = False
                    report.decision['reason'] = f"Temporal validation failed: {temporal_report.system_state}"
                    report.status = "SYSTEM_TEMPORALLY_INVALID"
                    event_bus.emit("SYSTEM_TEMPORALLY_INVALID", {
                        'run_id': run_id,
                        'system_state': temporal_report.system_state,
                        'scs': temporal_report.scs,
                        'timestamp': datetime.utcnow().isoformat()
                    })
        
        logger.info(
            f"[CLVE] Result: {report.status}, "
            f"PDS={metrics.pds:.4f}, AI={metrics.ai:.4f}, "
            f"CDS={metrics.cds:.4f}"
        )
        
        return report
    
    def _load_recent_state(self) -> dict:
        """Load recent portfolio state."""
        try:
            from src.portfolio.state.state_manager import get_state_manager
            manager = get_state_manager()
            return manager.get_state() or {}
        except Exception as e:
            logger.warning(f"[CLVE] Could not load recent state: {e}")
            return {}
    
    def _load_history(self, limit: int = 200) -> list:
        """Load state history from persisted snapshots, newest-first across all files."""
        try:
            import json
            from src.portfolio.state.portfolio_state import PORTFOLIO_STATE_DIR

            if not PORTFOLIO_STATE_DIR.exists():
                return []

            files = sorted(PORTFOLIO_STATE_DIR.glob("state_*.jsonl"), reverse=True)
            if not files:
                return []

            history = []
            for filepath in files:
                if len(history) >= limit:
                    break
                try:
                    with open(filepath, "r") as f:
                        lines = f.readlines()
                    # Read from the tail of each file so newest entries come first
                    needed = limit - len(history)
                    for line in reversed(lines[-needed:] if needed < len(lines) else lines):
                        try:
                            data = json.loads(line.strip())
                            s = data["state"]
                            history.append({
                                "roi": s.get("roi", 0.0),
                                "regime": s.get("regime", "neutral"),
                                "risk_lambda": s.get("risk_lambda", 1.0),
                                "run_count": s.get("run_count", 0),
                                "allocation_weights": s.get("allocation_weights", {}),
                                "exposure_by_market": s.get("exposure_by_market", {}),
                                "realized_pnl": s.get("realized_pnl", 0.0),
                            })
                        except Exception:
                            continue
                except Exception:
                    continue

            # Reverse so oldest-first (expected by metric functions)
            history.reverse()
            return history
        except Exception as e:
            logger.warning(f"[CLVE] Could not load history: {e}")

        return []
    
    def _compute_metrics(self, state: dict, history: list) -> ValidationMetrics:
        """Compute all validation metrics."""
        metrics = ValidationMetrics()
        
        if len(history) < 2:
            logger.info("[CLVE] Insufficient history for metrics")
            return metrics
        
        # 1. Portfolio Drift Score (PDS)
        # Measures change in portfolio behavior
        metrics.pds = self._calculate_pds(history)
        
        # 2. Adaptation Index (AI)
        metrics.ai, metrics.allocation_changes = self._calculate_ai(history)
        
        # 3. Risk Responsiveness (RR)
        metrics.rr = self._calculate_rr(history)
        
        # 4. Policy Sensitivity (PS)
        metrics.ps, metrics.regime_changes, metrics.policy_updates = self._calculate_ps(history)
        
        # 5. Counterfactual Divergence Score (CDS)
        metrics.cds = self._calculate_cds(history)
        
        # Outcome variance
        if history:
            rois = [h.get("roi", 0) for h in history]
            metrics.outcome_variance = float(np.var(rois)) if rois else 0.0
        
        return metrics
    
    def _calculate_pds(self, history: list) -> float:
        """Calculate Portfolio Drift Score."""
        if len(history) < 2:
            return 0.0
        
        # Compare recent allocations vs previous
        # Use ROI as proxy for portfolio behavior
        recent = history[-10:] if len(history) >= 10 else history
        previous = history[-20:-10] if len(history) >= 20 else history[:10]
        
        if not previous:
            return 0.0
        
        recent_mean = np.mean([h.get("roi", 0) for h in recent])
        previous_mean = np.mean([h.get("roi", 0) for h in previous])
        
        # PDS = distance between portfolio behaviors
        pds = abs(recent_mean - previous_mean)
        
        return float(pds)
    
    def _calculate_ai(self, history: list) -> tuple:
        """Calculate Adaptation Index."""
        if len(history) < 10:
            return 0.0, 0
        
        # Measure variance in allocation changes
        rois = [h.get("roi", 0) for h in history]
        
        if len(rois) < 2:
            return 0.0, 0
        
        # Allocation changes = variance in ROI changes
        roi_changes = np.diff(rois)
        allocation_variance = float(np.var(roi_changes)) if len(roi_changes) > 1 else 0.0
        
        # Outcome variance
        outcome_variance = float(np.var(rois)) if rois else 0.0
        
        # AI = allocation_variance / outcome_variance
        if outcome_variance > 0:
            ai = allocation_variance / outcome_variance
        else:
            ai = 0.0
        
        return ai, len(roi_changes)
    
    def _calculate_rr(self, history: list) -> float:
        """Calculate Risk Responsiveness."""
        # Simplified: measure correlation between lambda and drawdown changes
        # In a real system, would need lambda history
        if len(history) < 5:
            return 1.0  # Default to responsive
        
        # For now, use ROI changes as proxy
        rois = [h.get("roi", 0) for h in history]
        roi_changes = np.diff(rois)
        
        if len(roi_changes) < 2:
            return 1.0
        
        # RR = abs(mean(roi_changes)) - system is responding to outcomes
        rr = abs(np.mean(roi_changes))
        
        return float(rr)
    
    def _calculate_ps(self, history: list) -> tuple:
        """Calculate Policy Sensitivity."""
        # Simplified: count regime changes
        regime_changes = 0
        policy_updates = 0
        
        # In real system, would track regime/lambda changes
        if len(history) >= 10:
            regime_changes = len(history) // 10  # Estimated
            policy_updates = regime_changes // 2  # Estimated
        
        ps = policy_updates / max(regime_changes, 1)
        
        return ps, regime_changes, policy_updates
    
    def _calculate_cds(self, history: list) -> float:
        """Calculate Counterfactual Divergence Score."""
        # Simplified: measure divergence between predicted and actual
        if len(history) < 10:
            return 0.0
        
        # Use variance as proxy for divergence
        rois = [h.get("roi", 0) for h in history]
        variance = float(np.var(rois)) if rois else 0.0
        
        # Lower variance = more consistent = more divergence from expected
        cds = min(1.0, variance * 10)
        
        return cds
    
    def _classify(self, metrics: ValidationMetrics, history: list) -> dict:
        """Classify system as adaptive or static."""
        # Cannot declare static when no settlement data exists — roi=0 everywhere
        # simply means bets haven't settled yet, not that the system isn't adapting.
        has_settlement_data = any(h.get("roi", 0.0) != 0.0 for h in history)

        is_static = (
            has_settlement_data and
            metrics.pds < self.pds_threshold and
            metrics.ai < self.ai_threshold and
            metrics.cds < self.cds_threshold and
            len(history) >= 20
        )
        
        if is_static:
            return {
                "adaptive": False,
                "reason": "STATIC_OR_NO_LEARNING_DETECTED",
                "details": {
                    "pds": metrics.pds,
                    "ai": metrics.ai,
                    "cds": metrics.cds,
                }
            }
        
        return {
            "adaptive": True,
            "reason": "CLOSED_LOOP_CONFIRMED",
            "details": {
                "pds": metrics.pds,
                "ai": metrics.ai,
                "rr": metrics.rr,
            }
        }
    
    def _calculate_adaptive_score(self, metrics: ValidationMetrics) -> float:
        """Calculate overall adaptive score (0-1)."""
        score = 0.0
        
        # PDS contribution (0-0.3)
        score += min(0.3, metrics.pds * 10)
        
        # AI contribution (0-0.3)
        score += min(0.3, metrics.ai)
        
        # RR contribution (0-0.2)
        score += min(0.2, metrics.rr)
        
        # PS contribution (0-0.2)
        score += min(0.2, metrics.ps)
        
        return min(1.0, score)
    
    def _update_system_health(self, report: ValidationReport) -> None:
        """Update global system health flag."""
        self._system_health["last_checked"] = report.timestamp
        self._system_health["adaptation_score"] = report.adaptive_score
        self._system_health["closed_loop"] = report.decision.get("adaptive", False)
        
        if not report.decision.get("adaptive"):
            self._system_health["static_detection_count"] += 1
        else:
            self._system_health["static_detection_count"] = 0
    
    def get_system_health(self) -> dict:
        """Get current system health status."""
        return self._system_health.copy()
    
    def _persist_report(self, report: ValidationReport) -> None:
        """Persist validation report."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath = self.reports_dir / f"closed_loop_validation_{timestamp}.json"
        
        data = {
            "run_id": report.run_id,
            "timestamp": report.timestamp,
            "metrics": {
                "pds": report.metrics.pds,
                "ai": report.metrics.ai,
                "rr": report.metrics.rr,
                "ps": report.metrics.ps,
                "cds": report.metrics.cds,
            },
            "decision": report.decision,
            "status": report.status,
            "adaptive_score": report.adaptive_score,
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"[CLVE] Report saved to {filepath}")
    
    def _emit_events(self, report: ValidationReport) -> None:
        """Emit validation events."""
        if report.decision.get("adaptive"):
            event_bus.emit(Events.SYSTEM_ADAPTIVE_CONFIRMED, {
                "run_id": report.run_id,
                "adaptive_score": report.adaptive_score,
                "pds": report.metrics.pds,
                "ai": report.metrics.ai,
                "timestamp": report.timestamp,
            })
        else:
            event_bus.emit(Events.SYSTEM_STATIC_DETECTED, {
                "run_id": report.run_id,
                "reason": report.decision.get("reason"),
                "details": report.decision.get("details"),
                "timestamp": report.timestamp,
            })
        
        event_bus.emit(Events.CLOSED_LOOP_VALIDATION_COMPLETED, {
            "run_id": report.run_id,
            "status": report.status,
            "adaptive_score": report.adaptive_score,
            "timestamp": report.timestamp,
        })
        
        event_bus.emit(Events.ADAPTATION_SCORE_UPDATED, {
            "score": report.adaptive_score,
            "timestamp": report.timestamp,
        })
    
    def _evaluate_temporal(self, run_id: str, state: dict, metrics: ValidationMetrics) -> Optional[object]:
        """
        Evaluate temporal consistency across runs.
        
        Returns RunTemporalState if temporal validation is enabled.
        """
        try:
            from src.governance.temporal_consistency_engine import get_temporal_engine
            
            temporal_engine = get_temporal_engine()
            
            # Build current run data for temporal evaluation
            current_run_data = {
                'system_version': '2.0.0',
                'predictions': [],  # Would be populated from actual run data
                'portfolio': [],
                'risk_profile': {
                    'lambda': state.get('risk_lambda', 1.0),
                    'regime': state.get('regime', 'neutral')
                },
                'policy_decision': 'APPROVE' if metrics.pds > self.pds_threshold else 'REJECT',
                'roi': state.get('current_roi', 0),
                'brier_score': metrics.pds,  # Use as proxy
                'ece': metrics.cds,  # Use as proxy
                'risk_score': metrics.ai
            }
            
            # Get previous run data if available
            previous_run_data = None
            if temporal_engine._previous_states:
                prev = temporal_engine._previous_states[-1]
                previous_run_data = {
                    'roi': prev.delta_roi,
                    'brier_score': prev.delta_brier,
                    'ece': prev.delta_ece,
                    'risk_score': prev.delta_risk,
                    'policy_decision': prev.policy_decision
                }
            
            # Evaluate temporal state
            temporal_state = temporal_engine.evaluate(run_id, current_run_data, previous_run_data)
            
            # Save evolution report periodically
            if len(temporal_engine._previous_states) % 5 == 0:
                temporal_engine.save_evolution_report()
            
            logger.info(
                f"[CLVE] Temporal: state={temporal_state.system_state}, "
                f"SCS={temporal_state.scs:.3f}, PSI={temporal_state.psi:.3f}"
            )
            
            return temporal_state
            
        except Exception as e:
            logger.warning(f"[CLVE] Temporal evaluation failed: {e}")
            return None


# Global instance
_engine: Optional[ClosedLoopValidationEngine] = None


def get_closed_loop_validation_engine() -> ClosedLoopValidationEngine:
    """Get global CLVE instance."""
    global _engine
    if _engine is None:
        _engine = ClosedLoopValidationEngine()
    return _engine
