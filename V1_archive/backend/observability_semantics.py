from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import hashlib

from scripts.web_ui import RUN_SYSTEM_ACTIVATION_TIMESTAMP


SEMANTIC_VERSION = "1.0.0"

SEMANTIC_RULES = {
    "1.0.0": {
        "orphan_rules": {
            "legacy_only_condition": "legacy_orphans > 0 AND modern_orphans == 0",
            "modern_condition": "modern_orphans > 0",
            "none_condition": "no orphans"
        },
        "health_rules": {
            "broken_condition": "modern_orphans > 0 OR (failed_runs > 0 AND modern_predictions == 0)",
            "degraded_condition": "coverage < 50%",
            "healthy_condition": "all checks pass"
        },
        "warning_rules": {
            "warning": "modern_orphans OR broken health",
            "info": "legacy_only OR degraded health",
            "ok": "healthy state"
        },
        "epoch_boundary": RUN_SYSTEM_ACTIVATION_TIMESTAMP
    }
}


def get_rules_hash(version: str = None) -> str:
    """Get SHA256 hash of rules for a given version."""
    v = version or SEMANTIC_VERSION
    rules = SEMANTIC_RULES.get(v, SEMANTIC_RULES[SEMANTIC_VERSION])
    rules_str = str(sorted(rules.items()))
    return hashlib.sha256(rules_str.encode()).hexdigest()[:16]


@dataclass
class SemanticVersion:
    version: str = SEMANTIC_VERSION
    created_at: datetime = field(default_factory=datetime.utcnow)
    description: str = "Initial semantic versioning for observability layer"
    rules_hash: str = field(default_factory=lambda: get_rules_hash())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
            "rules_hash": self.rules_hash
        }


def init_observability_schema():
    """Create observability semantic snapshots table if not exists."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    with get_session() as s:
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS observability_semantic_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version VARCHAR(20) NOT NULL UNIQUE,
                rules_hash VARCHAR(16) NOT NULL,
                description TEXT,
                activation_timestamp DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        s.commit()
        
        existing = s.execute(text(
            "SELECT version FROM observability_semantic_snapshots WHERE version = :v"
        ), {"v": SEMANTIC_VERSION}).scalar()
        
        if not existing:
            s.execute(text("""
                INSERT INTO observability_semantic_snapshots 
                (version, rules_hash, description, activation_timestamp)
                VALUES (:v, :hash, :desc, :ts)
            """), {
                "v": SEMANTIC_VERSION,
                "hash": get_rules_hash(),
                "desc": "Initial semantic versioning for observability layer",
                "ts": RUN_SYSTEM_ACTIVATION_TIMESTAMP
            })
            s.commit()


class DataEpoch(Enum):
    LEGACY = "legacy"
    MODERN = "modern"


class OrphanState(Enum):
    NONE = "none"
    LEGACY_ONLY = "legacy_only"
    MODERN = "modern"


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BROKEN = "BROKEN"


class WarningLevel(Enum):
    OK = "OK"
    INFO = "INFO"
    WARNING = "WARNING"


@dataclass
class SemanticInterpretation:
    health_status: HealthStatus
    warning_level: WarningLevel
    orphan_state: OrphanState
    legacy_metrics: Dict[str, Any]
    modern_metrics: Dict[str, Any]
    interpretation: Dict[str, str]
    raw_metrics: Dict[str, Any]


class ObservabilitySemanticsEngine:
    """
    Centralized semantic interpretation engine.
    
    ALL UI components must use this engine for interpreting:
    - orphan state
    - health status  
    - warnings
    - coverage metrics
    - pipeline status
    
    No component is allowed to compute these independently.
    """
    
    def __init__(self, epoch_timestamp: str = None):
        self.epoch_timestamp = epoch_timestamp or RUN_SYSTEM_ACTIVATION_TIMESTAMP
    
    def get_epoch_boundary(self) -> datetime:
        """Return the epoch boundary timestamp."""
        return datetime.fromisoformat(self.epoch_timestamp.replace('Z', '+00:00'))
    
    def classify_data_epoch(self, created_at: datetime) -> DataEpoch:
        """Classify a record into its data epoch."""
        epoch = self.get_epoch_boundary()
        if created_at.replace(tzinfo=None) if created_at.tzinfo else created_at < epoch.replace(tzinfo=None):
            return DataEpoch.LEGACY
        return DataEpoch.MODERN
    
    def interpret_orphan_state(
        self,
        legacy_orphan_preds: int,
        legacy_orphan_bets: int,
        modern_orphan_preds: int,
        modern_orphan_bets: int
    ) -> tuple[OrphanState, str]:
        """
        Interpret orphan state with clear semantics.
        
        Rules:
        - IF legacy_orphans > 0 AND modern_orphans == 0: INFO (legacy only)
        - IF modern_orphans > 0: WARNING (actionable system issue)
        - IF none: OK
        """
        modern_total = modern_orphan_preds + modern_orphan_bets
        legacy_total = legacy_orphan_preds + legacy_orphan_bets
        
        if modern_total > 0:
            return (
                OrphanState.MODERN,
                f"{modern_total} orphan record(s) in modern epoch - system integrity issue"
            )
        elif legacy_total > 0:
            return (
                OrphanState.LEGACY_ONLY,
                f"{legacy_total} pre-RunContext records (non-operational, informational only)"
            )
        else:
            return (
                OrphanState.NONE,
                "No orphan records detected"
            )
    
    def interpret_warning_level(
        self,
        orphan_state: OrphanState,
        health_status: HealthStatus,
        pipeline_active: bool = True
    ) -> WarningLevel:
        """
        Determine warning level based on semantic rules.
        
        Rules:
        - WARNING = actionable system issue (modern orphans, broken health)
        - INFO = historical or expected condition (legacy only)
        - OK = healthy system state
        """
        if orphan_state == OrphanState.MODERN or health_status == HealthStatus.BROKEN:
            return WarningLevel.WARNING
        elif orphan_state == OrphanState.LEGACY_ONLY or health_status == HealthStatus.DEGRADED:
            return WarningLevel.INFO
        else:
            return WarningLevel.OK
    
    def interpret_health_status(
        self,
        modern_orphan_preds: int,
        modern_orphan_bets: int,
        modern_predictions: int,
        modern_bets: int,
        predictions_with_run: int,
        bets_with_run: int,
        active_runs: int,
        failed_runs: int
    ) -> HealthStatus:
        """
        Determine health status using ONLY modern epoch data.
        
        Rules:
        - BROKEN: modern orphans exist OR no modern data with failed runs
        - DEGRADED: coverage < 50% OR active runs with low coverage
        - HEALTHY: all checks pass
        """
        modern_orphans = modern_orphan_preds + modern_orphan_bets
        
        if modern_orphans > 0:
            return HealthStatus.BROKEN
        
        if modern_predictions > 0:
            pred_coverage = predictions_with_run / modern_predictions
            if pred_coverage < 0.5:
                return HealthStatus.DEGRADED
        
        if modern_bets > 0:
            bet_coverage = bets_with_run / modern_bets
            if bet_coverage < 0.5:
                return HealthStatus.DEGRADED
        
        if failed_runs > 0 and modern_predictions == 0:
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    def compute_all_semantics(self, raw_metrics: Dict[str, Any] = None) -> SemanticInterpretation:
        """Compute all semantics from raw metrics."""
        if raw_metrics is None:
            raw_metrics = self.fetch_raw_metrics()
        
        legacy_orphan_preds = raw_metrics.get('legacy_orphan_predictions', 0)
        legacy_orphan_bets = raw_metrics.get('legacy_orphan_bets', 0)
        modern_orphan_preds = raw_metrics.get('orphan_predictions', 0)
        modern_orphan_bets = raw_metrics.get('orphan_bets', 0)
        modern_predictions = raw_metrics.get('modern_predictions', 0)
        modern_bets = raw_metrics.get('modern_bets', 0)
        predictions_with_run = raw_metrics.get('predictions_with_run', 0)
        bets_with_run = raw_metrics.get('bets_with_run', 0)
        active_runs = raw_metrics.get('active_runs', 0)
        failed_runs = raw_metrics.get('failed_runs', 0)
        
        orphan_state, orphan_message = self.interpret_orphan_state(
            legacy_orphan_preds, legacy_orphan_bets,
            modern_orphan_preds, modern_orphan_bets
        )
        
        health_status = self.interpret_health_status(
            modern_orphan_preds, modern_orphan_bets,
            modern_predictions, modern_bets,
            predictions_with_run, bets_with_run,
            active_runs, failed_runs
        )
        
        warning_level = self.interpret_warning_level(
            orphan_state, health_status
        )
        
        legacy_metrics = {
            'predictions': raw_metrics.get('legacy_predictions', 0),
            'bets': raw_metrics.get('legacy_bets', 0),
            'orphan_predictions': legacy_orphan_preds,
            'orphan_bets': legacy_orphan_bets
        }
        
        modern_metrics = {
            'predictions': modern_predictions,
            'bets': modern_bets,
            'predictions_with_run': predictions_with_run,
            'bets_with_run': bets_with_run,
            'prediction_coverage_pct': raw_metrics.get('prediction_coverage_pct', 0),
            'bet_coverage_pct': raw_metrics.get('bet_coverage_pct', 0)
        }
        
        interpretation = {
            'orphan_state': orphan_message,
            'run_health': f"system is {health_status.value.lower()}",
            'pipeline_health': "active" if active_runs > 0 else "no active runs"
        }
        
        return SemanticInterpretation(
            health_status=health_status,
            warning_level=warning_level,
            orphan_state=orphan_state,
            legacy_metrics=legacy_metrics,
            modern_metrics=modern_metrics,
            interpretation=interpretation,
            raw_metrics=raw_metrics
        )
    
    def fetch_raw_metrics(self) -> Dict[str, Any]:
        """Fetch raw metrics from database."""
        from src.storage.db import get_session
        from sqlalchemy import text
        from datetime import datetime, timedelta
        
        epoch = self.epoch_timestamp
        
        with get_session() as s:
            active_runs = s.execute(text(
                "SELECT COUNT(*) FROM experiment_runs WHERE status = 'active'"
            )).scalar() or 0
            
            last_24h = datetime.utcnow() - timedelta(hours=24)
            completed_runs = s.execute(text(
                "SELECT COUNT(*) FROM experiment_runs WHERE status = 'completed' AND end_timestamp >= :ts"
            ), {"ts": last_24h.isoformat()}).scalar() or 0
            
            failed_runs = s.execute(text(
                "SELECT COUNT(*) FROM experiment_runs WHERE status = 'failed'"
            )).scalar() or 0
            
            legacy_predictions = s.execute(text(
                "SELECT COUNT(*) FROM prediction_records WHERE created_at < :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            legacy_bets = s.execute(text(
                "SELECT COUNT(*) FROM placed_bets WHERE placed_at < :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            modern_predictions = s.execute(text(
                "SELECT COUNT(*) FROM prediction_records WHERE created_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            modern_bets = s.execute(text(
                "SELECT COUNT(*) FROM placed_bets WHERE placed_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            orphan_predictions = s.execute(text(
                "SELECT COUNT(*) FROM prediction_records WHERE run_id IS NULL AND created_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            orphan_bets = s.execute(text(
                "SELECT COUNT(*) FROM placed_bets WHERE run_id IS NULL AND placed_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            legacy_orphan_predictions = s.execute(text(
                "SELECT COUNT(*) FROM prediction_records WHERE run_id IS NULL AND created_at < :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            legacy_orphan_bets = s.execute(text(
                "SELECT COUNT(*) FROM placed_bets WHERE run_id IS NULL AND placed_at < :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            predictions_with_run = s.execute(text(
                "SELECT COUNT(*) FROM prediction_records WHERE run_id IS NOT NULL AND created_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            bets_with_run = s.execute(text(
                "SELECT COUNT(*) FROM placed_bets WHERE run_id IS NOT NULL AND placed_at >= :epoch"
            ), {"epoch": epoch}).scalar() or 0
            
            return {
                "active_runs": active_runs,
                "completed_runs_24h": completed_runs,
                "failed_runs": failed_runs,
                "legacy_predictions": legacy_predictions,
                "legacy_bets": legacy_bets,
                "modern_predictions": modern_predictions,
                "modern_bets": modern_bets,
                "orphan_predictions": orphan_predictions,
                "orphan_bets": orphan_bets,
                "legacy_orphan_predictions": legacy_orphan_predictions,
                "legacy_orphan_bets": legacy_orphan_bets,
                "predictions_with_run": predictions_with_run,
                "bets_with_run": bets_with_run,
                "prediction_coverage_pct": round(predictions_with_run / modern_predictions * 100, 1) if modern_predictions > 0 else 0,
                "bet_coverage_pct": round(bets_with_run / modern_bets * 100, 1) if modern_bets > 0 else 0
            }
    
    def to_api_response(self, semantics: SemanticInterpretation) -> Dict[str, Any]:
        """
        Convert semantics to standardized API response format.
        
        All UI components should use this format.
        """
        return {
            "semantic_version": SEMANTIC_VERSION,
            "interpretation_version_hash": get_rules_hash(),
            "health_status": semantics.health_status.value,
            "warning_level": semantics.warning_level.value,
            "legacy_metrics": semantics.legacy_metrics,
            "modern_metrics": semantics.modern_metrics,
            "interpretation": semantics.interpretation,
            "raw_metrics": {
                "active_runs": semantics.raw_metrics.get('active_runs', 0),
                "failed_runs": semantics.raw_metrics.get('failed_runs', 0),
                "completed_runs_24h": semantics.raw_metrics.get('completed_runs_24h', 0)
            }
        }


def get_observability_semantics(
    version: str = None,
    replay_mode: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to get standardized observability semantics.
    
    Args:
        version: Specific semantic version to use (for historical replay)
        replay_mode: If True, use specified version for historical evaluation
    
    All UI components should use this function instead of computing metrics locally.
    """
    try:
        init_observability_schema()
    except Exception:
        pass
    
    engine = ObservabilitySemanticsEngine(
        epoch_timestamp=SEMANTIC_RULES.get(version, SEMANTIC_RULES[SEMANTIC_VERSION]).get("epoch_boundary") 
        if version else None
    )
    semantics = engine.compute_all_semantics()
    return engine.to_api_response(semantics)