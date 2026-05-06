#!/usr/bin/env python3
"""
src/governance/ui_semantic_auto_healing_engine.py

UI Semantic Auto-Healing Engine - automatically corrects UI interpretation mismatches.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)


class HealingMode(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    HEALING = "HEALING"
    LOCKED = "LOCKED"


class HealingAction(Enum):
    METRIC_NORMALIZATION = "METRIC_NORMALIZATION"
    AGGREGATION_CORRECTION = "AGGREGATION_CORRECTION"
    TIME_WINDOW_FIX = "TIME_WINDOW_FIX"
    CONTRACT_VERSION_FORCE = "CONTRACT_VERSION_FORCE"
    INTERPRETATION_PATCH = "INTERPRETATION_PATCH"


@dataclass
class HealingPatch:
    """Represents a patch applied to fix semantic divergence."""
    dashboard_id: str
    field_name: str
    action: HealingAction
    old_value: Any
    new_value: Any
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class HealingReport:
    """Report of healing actions taken."""
    dashboard_id: str
    mode: HealingMode
    patches: list = field(default_factory=list)
    healing_triggered: bool = False
    status: str = "SUCCESS"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class MetricNormalizer:
    """Handles metric normalization repairs."""
    
    @staticmethod
    def normalize_probability(value: Any, semantic: str = "calibrated_probability") -> float:
        """Ensure probability is in valid range [0, 1]."""
        try:
            val = float(value)
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return 0.5  # Default to neutral probability
    
    @staticmethod
    def normalize_ev(value: Any) -> float:
        """Normalize expected value - can be negative."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    @staticmethod
    def normalize_adaptive(value: Any) -> dict:
        """Convert boolean to proper adaptive semantic (measurable parameter change)."""
        if isinstance(value, bool):
            # Boolean is wrong interpretation - convert to delta info
            return {
                "requires_delta": True,
                "warning": "Boolean interpretation detected, should be parameter delta"
            }
        return value
    
    @staticmethod
    def normalize_lambda(value: Any) -> float:
        """Normalize lambda - it's a multiplier, not a score."""
        try:
            val = float(value)
            return max(0.0, min(2.0, val))  # Reasonable multiplier range
        except (TypeError, ValueError):
            return 1.0
    
    @staticmethod
    def normalize_stability_metric(value: Any) -> float:
        """Normalize stability metrics (PSI, PDI, SCS) to [0, 1]."""
        try:
            val = float(value)
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return 0.5


class AggregationCorrector:
    """Handles aggregation correction."""
    
    AGGREGATION_RULES = {
        "market_breakdown": "sum",
        "total": "sum",
        "score": "mean",
        "total_exposure": "sum",
        "runs": "list",
        "job_count": "count"
    }
    
    @classmethod
    def correct_aggregation(cls, data: Any, field: str, rule: str) -> Any:
        """Apply correct aggregation based on rule."""
        if rule == "sum":
            try:
                return float(data) if isinstance(data, (int, float)) else 0.0
            except:
                return 0.0
        elif rule == "mean":
            try:
                return float(data) if isinstance(data, (int, float)) else 0.0
            except:
                return 0.0
        elif rule == "list":
            return data if isinstance(data, list) else []
        elif rule == "count":
            try:
                return int(data) if isinstance(data, (int, float)) else len(data) if hasattr(data, '__len__') else 0
            except:
                return 0
        return data


class UISemanticAutoHealingEngine:
    """
    Automatically corrects UI interpretation mismatches at runtime.
    
    Strategies:
    1. Metric Normalization Repair
    2. Aggregation Correction
    3. Time Window Alignment Fix
    4. Contract Version Repair
    """
    
    CURRENT_CONTRACT_VERSION = "v1"
    
    def __init__(self, mode: HealingMode = HealingMode.HEALTHY):
        self.mode = mode
        self._patch_history: list[HealingPatch] = []
        self._healing_count = 0
        self._correction_overrides: dict[str, Callable] = {}
        
        # Register default corrections
        self._register_default_corrections()
        
        logger.info(f"[HEAL] Auto-healing engine initialized in mode: {mode.value}")
    
    def _register_default_corrections(self):
        """Register default correction functions."""
        self._correction_overrides = {
            "probability": MetricNormalizer.normalize_probability,
            "ev": MetricNormalizer.normalize_ev,
            "adaptive": MetricNormalizer.normalize_adaptive,
            "lambda": MetricNormalizer.normalize_lambda,
            "psi": MetricNormalizer.normalize_stability_metric,
            "pdi": MetricNormalizer.normalize_stability_metric,
            "scs": MetricNormalizer.normalize_stability_metric,
        }
    
    def set_mode(self, mode: HealingMode):
        """Change healing mode."""
        old_mode = self.mode
        self.mode = mode
        logger.info(f"[HEAL] Mode changed: {old_mode.value} → {mode.value}")
        
        # Emit event
        self._emit_healing_event("UI_AUTO_HEAL_TRIGGERED", {
            "old_mode": old_mode.value,
            "new_mode": mode.value
        })
    
    def heal_divergence(self, dashboard_id: str, divergences: list, data: dict) -> tuple[dict, HealingReport]:
        """
        Apply healing to fix semantic divergences.
        
        Args:
            dashboard_id: The dashboard with divergence
            divergences: List of divergence reports
            data: Current data that needs healing
            
        Returns:
            Tuple of (healed_data, healing_report)
        """
        if self.mode == HealingMode.HEALTHY:
            return data, HealingReport(dashboard_id=dashboard_id, mode=self.mode)
        
        patches = []
        
        # Only heal in HEALING or LOCKED modes
        if self.mode not in [HealingMode.HEALING, HealingMode.LOCKED]:
            return data, HealingReport(
                dashboard_id=dashboard_id,
                mode=self.mode,
                status="SKIPPED",
                patches=patches
            )
        
        healed_data = data.copy()
        
        for div in divergences:
            field_name = div.get("field", "")
            expected = div.get("expected", "")
            actual = div.get("actual", "")
            divergence_type = div.get("type", "")
            
            patch = None
            
            # Apply appropriate healing strategy
            if divergence_type == "METRIC_INTERPRETATION":
                patch = self._heal_metric(healed_data, field_name, expected)
            elif divergence_type == "AGGREGATION_MISMATCH":
                patch = self._heal_aggregation(healed_data, field_name, expected)
            elif divergence_type == "CONTRACT_VERSION_MISMATCH":
                patch = self._heal_contract_version(healed_data)
            
            if patch:
                patches.append(patch)
                self._patch_history.append(patch)
                self._healing_count += 1
        
        # Emit healing event
        if patches:
            self._emit_healing_event("UI_INTERPRETATION_PATCHED", {
                "dashboard_id": dashboard_id,
                "patch_count": len(patches)
            })
        
        report = HealingReport(
            dashboard_id=dashboard_id,
            mode=self.mode,
            patches=[{
                "field": p.field_name,
                "action": p.action.value,
                "old_value": p.old_value,
                "new_value": p.new_value
            } for p in patches],
            healing_triggered=len(patches) > 0,
            status="SUCCESS" if patches else "NO_PATCHES"
        )
        
        return healed_data, report
    
    def _heal_metric(self, data: dict, field_name: str, expected_semantic: str) -> Optional[HealingPatch]:
        """Heal metric interpretation."""
        if field_name not in data:
            return None
        
        old_value = data[field_name]
        
        # Get correction function
        corrector = self._correction_overrides.get(field_name)
        if not corrector:
            # Try by semantic
            corrector = self._correction_overrides.get(expected_semantic)
        
        if corrector:
            try:
                new_value = corrector(old_value)
                data[field_name] = new_value
                
                return HealingPatch(
                    dashboard_id="",
                    field_name=field_name,
                    action=HealingAction.METRIC_NORMALIZATION,
                    old_value=str(old_value),
                    new_value=str(new_value),
                    reason=f"Normalized {field_name} to {expected_semantic}"
                )
            except Exception as e:
                logger.warning(f"[HEAL] Failed to heal metric {field_name}: {e}")
        
        return None
    
    def _heal_aggregation(self, data: dict, field_name: str, expected_rule: str) -> Optional[HealingPatch]:
        """Heal aggregation mismatch."""
        if field_name not in data:
            return None
        
        old_value = data[field_name]
        
        # Apply correct aggregation
        new_value = AggregationCorrector.correct_aggregation(old_value, field_name, expected_rule)
        data[field_name] = new_value
        
        return HealingPatch(
            dashboard_id="",
            field_name=field_name,
            action=HealingAction.AGGREGATION_CORRECTION,
            old_value=str(old_value),
            new_value=str(new_value),
            reason=f"Corrected aggregation to {expected_rule}"
        )
    
    def _heal_contract_version(self, data: dict) -> Optional[HealingPatch]:
        """Force contract version alignment."""
        old_version = data.get("render_contract_version", "unknown")
        new_version = self.CURRENT_CONTRACT_VERSION
        
        if old_version != new_version:
            data["render_contract_version"] = new_version
            
            return HealingPatch(
                dashboard_id="",
                field_name="render_contract_version",
                action=HealingAction.CONTRACT_VERSION_FORCE,
                old_value=old_version,
                new_value=new_version,
                reason=f"Forced contract version from {old_version} to {new_version}"
            )
        
        return None
    
    def _emit_healing_event(self, event_name: str, data: dict):
        """Emit healing events."""
        from src.alerts.event_bus import event_bus
        
        event_bus.emit(event_name, {
            **data,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def get_healing_stats(self) -> dict:
        """Get healing statistics."""
        return {
            "mode": self.mode.value,
            "total_heals": self._healing_count,
            "patch_count": len(self._patch_history),
            "recent_patches": len(self._patch_history[-10:]) if self._patch_history else 0
        }
    
    def get_patch_history(self, limit: int = 20) -> list:
        """Get recent patch history."""
        return [
            {
                "dashboard": p.dashboard_id,
                "field": p.field_name,
                "action": p.action.value,
                "old": p.old_value,
                "new": p.new_value,
                "timestamp": p.timestamp
            }
            for p in self._patch_history[-limit:]
        ]


# Global instance
_healing_engine: Optional[UISemanticAutoHealingEngine] = None


def get_healing_engine() -> UISemanticAutoHealingEngine:
    """Get global healing engine."""
    global _healing_engine
    if _healing_engine is None:
        _healing_engine = UISemanticAutoHealingEngine()
    return _healing_engine


def heal_dashboard(dashboard_id: str, divergences: list, data: dict) -> dict:
    """Apply healing to dashboard data."""
    engine = get_healing_engine()
    healed_data, report = engine.heal_divergence(dashboard_id, divergences, data)
    return healed_data


def set_healing_mode(mode: str):
    """Set healing mode."""
    engine = get_healing_engine()
    engine.set_mode(HealingMode(mode))