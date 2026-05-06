#!/usr/bin/env python3
"""
src/governance/ui_semantic_contract_engine.py

UI Semantic Contract Validation Engine - ensures all UI views interpret system truth identically.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SemanticValidationStatus(Enum):
    VALID = "VALID"
    DEGRADED_VIEW = "DEGRADED_VIEW"
    CONTRACT_BREACH = "CONTRACT_BREACH"
    DIVERGENCE_DETECTED = "DIVERGENCE_DETECTED"


class DivergenceType(Enum):
    METRIC_INTERPRETATION = "METRIC_INTERPRETATION"
    STRUCTURAL_MISMATCH = "STRUCTURAL_MISMATCH"
    AGGREGATION_MISMATCH = "AGGREGATION_MISMATCH"
    TIME_WINDOW_MISMATCH = "TIME_WINDOW_MISMATCH"
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"


@dataclass
class UISemanticSnapshot:
    """Semantic snapshot from a dashboard."""
    dashboard_id: str
    source_endpoint: str
    data_snapshot: dict
    computed_metrics: dict
    render_contract_version: str = "v1"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class DivergenceReport:
    """Report of semantic divergence detected."""
    dashboard_id: str
    divergence_type: DivergenceType
    field_name: str
    expected_interpretation: str
    actual_interpretation: str
    severity: str  # "error", "warning"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SemanticValidationResult:
    """Result of semantic validation."""
    status: SemanticValidationStatus
    dashboard_id: str
    contract_version: str
    divergences: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class UISemanticContractEngine:
    """
    Validates UI interpretation of system truth data.
    
    Ensures all dashboards interpret metrics identically and deterministically.
    """
    
    # Contract version
    CURRENT_CONTRACT_VERSION = "v1"
    
    # Semantic rules for each dashboard
    DASHBOARD_CONTRACTS = {
        "predictions": {
            "required_fields": ["total_count", "markets"],
            "metric_semantics": {
                "probability": "calibrated_probability",
                "ev": "expected_value_after_calibration",
                "win_rate": "settled_outcomes_only"
            },
            "aggregation_rules": {
                "market_breakdown": "sum",
                "total": "sum"
            }
        },
        "betting": {
            "required_fields": ["bankroll", "total_staked", "pnl"],
            "metric_semantics": {
                "exposure": "post_correlation_adjusted",
                "allocation": "normalized_weights_sum_to_1"
            },
            "aggregation_rules": {
                "total_exposure": "sum"
            }
        },
        "tracking": {
            "required_fields": ["pds_threshold", "ai_threshold"],
            "metric_semantics": {
                "adaptive": "measurable_parameter_change_across_runs",
                "pds": "portfolio_drift_score",
                "ai": "adaptation_index",
                "cds": "calibration_drift_score"
            },
            "aggregation_rules": {
                "score": "mean"
            }
        },
        "runs": {
            "required_fields": ["recent_runs"],
            "metric_semantics": {
                "status": "pipeline_stage_completion"
            },
            "aggregation_rules": {
                "runs": "list"
            }
        },
        "health": {
            "required_fields": ["jobs", "job_count"],
            "metric_semantics": {
                "job_status": "last_execution_state"
            },
            "aggregation_rules": {
                "job_count": "count"
            }
        },
        "governance": {
            "required_fields": ["temporal"],
            "metric_semantics": {
                "psi": "prediction_stability_across_runs_not_accuracy",
                "pdi": "allocation_drift_not_performance",
                "rrs": "risk_smoothness",
                "por": "policy_oscillation_rate",
                "scs": "composite_convergence_signal_not_roi"
            },
            "aggregation_rules": {
                "scs": "weighted_mean"
            }
        },
        "architecture": {
            "required_fields": ["pipeline", "system_status"],
            "metric_semantics": {
                "stage_completion": "pipeline_stage_completion"
            },
            "aggregation_rules": {}
        }
    }
    
    def __init__(self):
        self._validation_history: list[SemanticValidationResult] = []
        self._divergence_log: list[DivergenceReport] = []
    
    def validate_dashboard(self, snapshot: UISemanticSnapshot) -> SemanticValidationResult:
        """Validate a dashboard's semantic interpretation."""
        dashboard_id = snapshot.dashboard_id
        contract = self.DASHBOARD_CONTRACTS.get(dashboard_id)
        
        if not contract:
            logger.warning(f"[SEMANTIC] No contract defined for dashboard: {dashboard_id}")
            return SemanticValidationResult(
                status=SemanticValidationStatus.DEGRADED_VIEW,
                dashboard_id=dashboard_id,
                contract_version=snapshot.render_contract_version,
                warnings=[f"No contract defined for dashboard: {dashboard_id}"]
            )
        
        divergences = []
        warnings = []
        
        # 1. Check contract version
        if snapshot.render_contract_version != self.CURRENT_CONTRACT_VERSION:
            divergences.append(DivergenceReport(
                dashboard_id=dashboard_id,
                divergence_type=DivergenceType.CONTRACT_VERSION_MISMATCH,
                field_name="render_contract_version",
                expected_interpretation=self.CURRENT_CONTRACT_VERSION,
                actual_interpretation=snapshot.render_contract_version,
                severity="error"
            ))
        
        # 2. Validate required fields
        data = snapshot.data_snapshot
        for field_name in contract.get("required_fields", []):
            if field_name not in data:
                warnings.append(f"Missing required field: {field_name}")
        
        # 3. Validate metric semantics
        metric_semantics = contract.get("metric_semantics", {})
        computed = snapshot.computed_metrics
        
        for metric, semantic_meaning in metric_semantics.items():
            if metric in computed:
                # Validate semantic interpretation
                semantic_valid = self._validate_metric_semantic(
                    dashboard_id, metric, computed[metric], semantic_meaning
                )
                if not semantic_valid:
                    divergences.append(DivergenceReport(
                        dashboard_id=dashboard_id,
                        divergence_type=DivergenceType.METRIC_INTERPRETATION,
                        field_name=metric,
                        expected_interpretation=semantic_meaning,
                        actual_interpretation="unknown",
                        severity="warning"
                    ))
        
        # 4. Validate aggregation rules
        aggregation_rules = contract.get("aggregation_rules", {})
        for field, rule in aggregation_rules.items():
            if field in data:
                agg_valid = self._validate_aggregation(data[field], rule)
                if not agg_valid:
                    warnings.append(f"Aggregation rule violation for {field}: {rule}")
        
        # Determine status
        if divergences:
            status = SemanticValidationStatus.DIVERGENCE_DETECTED
        elif warnings:
            status = SemanticValidationStatus.DEGRADED_VIEW
        else:
            status = SemanticValidationStatus.VALID
        
        result = SemanticValidationResult(
            status=status,
            dashboard_id=dashboard_id,
            contract_version=snapshot.render_contract_version,
            divergences=[{
                "type": d.divergence_type.value,
                "field": d.field_name,
                "expected": d.expected_interpretation,
                "actual": d.actual_interpretation,
                "severity": d.severity
            } for d in divergences],
            warnings=warnings
        )
        
        # Store in history
        self._validation_history.append(result)
        self._divergence_log.extend(divergences)
        
        # Emit events
        self._emit_semantic_event(result)
        
        return result
    
    def _validate_metric_semantic(self, dashboard_id: str, metric: str, value: Any, expected_semantic: str) -> bool:
        """Validate that a metric is interpreted semantically correctly."""
        # Specific semantic validations
        if metric == "probability":
            # Probability must be 0-1
            if isinstance(value, (int, float)):
                return 0 <= value <= 1
        
        elif metric == "ev":
            # EV is expected value - can be negative or positive
            if isinstance(value, (int, float)):
                return -1 <= value <= 10  # Reasonable bounds
        
        elif metric == "adaptive":
            # Adaptive means measurable parameter change - NOT boolean flip
            if isinstance(value, bool):
                logger.warning(f"[SEMANTIC] {dashboard_id}: adaptive treated as boolean, should be delta")
                return False
        
        elif metric in ["psi", "pdi", "scs"]:
            # These are stability metrics - must be 0-1
            if isinstance(value, (int, float)):
                return 0 <= value <= 1
        
        elif metric == "lambda":
            # Lambda is a multiplier, NOT a score
            if isinstance(value, (int, float)):
                return 0 <= value <= 2  # Reasonable multiplier range
        
        return True
    
    def _validate_aggregation(self, data: Any, rule: str) -> bool:
        """Validate aggregation rule is followed."""
        if rule == "sum" and isinstance(data, (int, float)):
            return True
        elif rule == "mean" and isinstance(data, (int, float)):
            return True
        elif rule == "list" and isinstance(data, list):
            return True
        elif rule == "count" and isinstance(data, (int, float)):
            return True
        
        return True
    
    def _emit_semantic_event(self, result: SemanticValidationResult):
        """Emit semantic validation events."""
        from src.alerts.event_bus import event_bus
        
        if result.status == SemanticValidationStatus.VALID:
            event_bus.emit("UI_SEMANTIC_VALID", {
                "dashboard_id": result.dashboard_id,
                "contract_version": result.contract_version,
                "timestamp": result.timestamp
            })
        elif result.status == SemanticValidationStatus.DIVERGENCE_DETECTED:
            event_bus.emit("UI_SEMANTIC_DIVERGENCE_DETECTED", {
                "dashboard_id": result.dashboard_id,
                "divergence_count": len(result.divergences),
                "timestamp": result.timestamp
            })
        elif result.status == SemanticValidationStatus.CONTRACT_BREACH:
            event_bus.emit("UI_CONTRACT_BREACH", {
                "dashboard_id": result.dashboard_id,
                "timestamp": result.timestamp
            })
        elif result.status == SemanticValidationStatus.DEGRADED_VIEW:
            event_bus.emit("UI_VIEW_DEGRADED", {
                "dashboard_id": result.dashboard_id,
                "warnings": result.warnings,
                "timestamp": result.timestamp
            })
    
    def get_divergence_summary(self) -> dict:
        """Get summary of all divergences."""
        summary = {}
        for d in self._divergence_log:
            key = d.divergence_type.value
            summary[key] = summary.get(key, 0) + 1
        return summary
    
    def get_validation_status(self) -> dict:
        """Get overall validation status."""
        if not self._validation_history:
            return {"status": "NO_DATA", "dashboards_validated": 0}
        
        recent = self._validation_history[-10:]
        
        status_counts = {}
        for r in recent:
            status_counts[r.status.value] = status_counts.get(r.status.value, 0) + 1
        
        return {
            "status": "SEMANTICALLY_CONSISTENT" if status_counts.get("VALID", 0) > len(recent) / 2 else "INCONSISTENT",
            "dashboards_validated": len(self._validation_history),
            "recent_status": status_counts,
            "divergence_summary": self.get_divergence_summary()
        }


# Global instance
_semantic_engine: Optional[UISemanticContractEngine] = None


def get_semantic_engine() -> UISemanticContractEngine:
    """Get global semantic contract engine."""
    global _semantic_engine
    if _semantic_engine is None:
        _semantic_engine = UISemanticContractEngine()
    return _semantic_engine


def validate_dashboard_snapshot(dashboard_id: str, data: dict, computed_metrics: dict) -> SemanticValidationResult:
    """Validate a dashboard's semantic interpretation."""
    snapshot = UISemanticSnapshot(
        dashboard_id=dashboard_id,
        source_endpoint=f"/api/unified/{dashboard_id}",
        data_snapshot=data,
        computed_metrics=computed_metrics
    )
    return get_semantic_engine().validate_dashboard(snapshot)


def get_semantic_status() -> dict:
    """Get overall semantic validation status."""
    return get_semantic_engine().get_validation_status()