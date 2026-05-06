#!/usr/bin/env python3
"""
src/governance/system_versioning.py

System versioning for full lineage tracking.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_VERSION = "2.0.0"


@dataclass
class SystemVersion:
    """Complete system version tracking."""
    model_version: str
    calibration_version: str
    data_version: str
    decision_version: str
    policy_version: str
    system_version: str = field(default=SYSTEM_VERSION)
    
    @classmethod
    def create(cls, model: str = "unknown", calibration: str = "unknown", 
               data: str = "unknown", decision: str = "unknown", policy: str = "unknown"):
        return cls(
            model_version=model,
            calibration_version=calibration,
            data_version=data,
            decision_version=decision,
            policy_version=policy,
            system_version=SYSTEM_VERSION
        )
    
    def composite_version(self) -> str:
        return f"{self.model_version}_{self.calibration_version}_{self.data_version}_{self.decision_version}_{self.policy_version}"
    
    def with_updated(self, **kwargs) -> 'SystemVersion':
        """Create new version with updated fields."""
        updates = {
            'model_version': self.model_version,
            'calibration_version': self.calibration_version,
            'data_version': self.data_version,
            'decision_version': self.decision_version,
            'policy_version': self.policy_version,
            'system_version': self.system_version
        }
        updates.update(kwargs)
        return SystemVersion(**updates)
    
    def to_dict(self) -> dict:
        return {
            'model_version': self.model_version,
            'calibration_version': self.calibration_version,
            'data_version': self.data_version,
            'decision_version': self.decision_version,
            'policy_version': self.policy_version,
            'system_version': self.system_version,
            'composite': self.composite_version()
        }


@dataclass  
class RunLineage:
    """Tracks lineage of a single run through the pipeline."""
    run_id: str
    system_version: str
    
    prediction_ids: list = field(default_factory=list)
    prediction_count: int = 0
    bet_count: int = 0
    portfolio_id: Optional[str] = None
    risk_id: Optional[str] = None
    policy_id: Optional[str] = None
    execution_id: Optional[str] = None
    
    model_version: Optional[str] = None
    calibration_version: Optional[str] = None
    decision_version: Optional[str] = None
    policy_version: Optional[str] = None
    
    experiment: bool = False
    strategy_variant: str = "baseline"
    
    start_time: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    end_time: Optional[str] = None
    status: str = "STARTED"
    health_score: float = 0.0
    
    def add_prediction_id(self, pred_id: str):
        self.prediction_ids.append(pred_id)
    
    def set_portfolio(self, portfolio_id: str):
        self.portfolio_id = portfolio_id
    
    def set_risk(self, risk_id: str, decision_version: str):
        self.risk_id = risk_id
        self.decision_version = decision_version
    
    def set_policy(self, policy_id: str, policy_version: str):
        self.policy_id = policy_id
        self.policy_version = policy_version
    
    def set_execution(self, execution_id: str):
        self.execution_id = execution_id
    
    def complete(self, status: str = "COMPLETE"):
        self.end_time = datetime.utcnow().isoformat()
        self.status = status
    
    def to_dict(self) -> dict:
        return {
            'run_id': self.run_id,
            'system_version': self.system_version,
            'prediction_count': self.prediction_count,
            'bet_count': self.bet_count,
            'prediction_ids': self.prediction_ids,
            'portfolio_id': self.portfolio_id,
            'risk_id': self.risk_id,
            'policy_id': self.policy_id,
            'execution_id': self.execution_id,
            'model_version': self.model_version,
            'calibration_version': self.calibration_version,
            'decision_version': self.decision_version,
            'policy_version': self.policy_version,
            'experiment': self.experiment,
            'strategy_variant': self.strategy_variant,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'status': self.status,
            'health_score': self.health_score
        }
    
    def to_trace_summary(self) -> str:
        return f"""Run: {self.run_id}
Status: {self.status}
System: {self.system_version}
Predictions: {len(self.prediction_ids)}
Portfolio: {self.portfolio_id}
Risk: {self.risk_id}
Policy: {self.policy_id}
Execution: {self.execution_id}
Versions: model={self.model_version}, calibration={self.calibration_version}, decision={self.decision_version}, policy={self.policy_version}"""


class VersionManager:
    """Manages system versioning across runs."""
    
    _current: Optional[SystemVersion] = None
    
    @classmethod
    def get_current(cls) -> SystemVersion:
        if cls._current is None:
            cls._current = cls._build_from_db()
        return cls._current

    @classmethod
    def _build_from_db(cls) -> SystemVersion:
        """Read active model/calibration versions from DB."""
        try:
            from src.storage.db import get_session
            from sqlalchemy import text
            with get_session() as s:
                rows = s.execute(text(
                    "SELECT market, version_label FROM model_versions WHERE is_active=1"
                )).fetchall()
            labels = {r[0]: r[1] for r in rows}
            # e.g. "v02-v04-v02-v02" (h2h-btts-ou25-ou15)
            def short(lbl: str) -> str:
                return lbl.split("_")[0] if lbl else "?"
            def cal(lbl: str) -> str:
                parts = lbl.split("_")
                return parts[1] if len(parts) > 1 else "c00"
            markets = ["h2h", "btts", "ou25", "ou15"]
            model_ver = "-".join(short(labels.get(m, "?")) for m in markets)
            cal_ver = "-".join(cal(labels.get(m, "?")) for m in markets)
            return SystemVersion.create(model=model_ver, calibration=cal_ver)
        except Exception:
            return SystemVersion.create()
    
    @classmethod
    def update(cls, **kwargs) -> SystemVersion:
        cls._current = cls.get_current().with_updated(**kwargs)
        logger.info(f"[VERSION] Updated to: {cls._current.composite_version()}")
        return cls._current
    
    @classmethod
    def create_lineage(cls, run_id: str) -> RunLineage:
        current = cls.get_current()
        return RunLineage(
            run_id=run_id,
            system_version=current.system_version,
            model_version=current.model_version,
            calibration_version=current.calibration_version,
            decision_version=current.decision_version,
            policy_version=current.policy_version
        )


def get_system_version() -> SystemVersion:
    """Get current system version."""
    return VersionManager.get_current()


def create_run_lineage(run_id: str) -> RunLineage:
    """Create new lineage tracker for a run."""
    return VersionManager.create_lineage(run_id)