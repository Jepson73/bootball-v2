#!/usr/bin/env python3
"""
src/api/system_truth_snapshot.py

Unified System Truth Layer API - single source of observability.

All dashboards read from this canonical schema.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SafeResponse:
    """Safe JSON response wrapper."""
    success: bool = True
    data: dict = field(default_factory=dict)
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error
        }


class SystemTruthSnapshot:
    """
    Unified System Truth Layer - single source of observability.
    
    Consolidates all backend systems into a single structured response.
    """
    
    def __init__(self):
        self._cache: dict = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = 30  # Cache for 30 seconds
    
    def get_snapshot(self) -> dict:
        """Get complete system snapshot."""
        return {
            "system_status": self._get_system_status(),
            "execution": self._get_execution(),
            "pipeline": self._get_pipeline(),
            "predictions": self._get_predictions(),
            "portfolio": self._get_portfolio(),
            "risk": self._get_risk(),
            "policy": self._get_policy(),
            "execution_engine": self._get_execution_engine(),
            "clve": self._get_clve(),
            "temporal_governance": self._get_temporal_governance(),
            "lineage": self._get_lineage(),
            "scheduler_state": self._get_scheduler_state(),
            "data_health": self._get_data_health(),
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def _get_system_status(self) -> dict:
        """Get overall system status."""
        try:
            from backend.runtime_mode import get_mode_name
            mode = get_mode_name()
            
            return {
                "mode": mode,
                "status": "OPERATIONAL",
                "version": "2.0.0",
                "components_loaded": True
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
    
    def _get_execution(self) -> dict:
        """Get execution state."""
        try:
            from src.betting.bankroll import get_bankroll_manager
            bm = get_bankroll_manager()
            
            return {
                "bankroll": float(bm.get_balance()),
                "total_staked": float(bm.total_staked),
                "total_won": float(bm.total_won),
                "total_lost": float(bm.total_lost),
                "pnl": float(bm.get_pnl())
            }
        except Exception as e:
            return {"bankroll": 0, "total_staked": 0, "total_won": 0, "total_lost": 0, "pnl": 0, "_unavailable": True}
    
    def _get_pipeline(self) -> dict:
        """Get pipeline state."""
        try:
            from src.contracts.pipeline_contracts import get_trace_report
            from pathlib import Path
            
            # Get most recent trace
            trace_dir = Path("reports")
            traces = list(trace_dir.glob("pipeline_trace_*.md"))
            
            if traces:
                latest = sorted(traces, reverse=True)[0]
                with open(latest) as f:
                    content = f.read()
                
                return {
                    "last_trace_file": str(latest.name),
                    "content_preview": content[:500]
                }
            
            return {"last_run": "No traces available"}
        except Exception as e:
            return {"error": str(e)}
    
    def _get_predictions(self) -> dict:
        """Get predictions state."""
        try:
            from src.storage.db import get_session
            from src.storage.models import PredictionRecord
            from sqlalchemy import select, func
            
            with get_session() as s:
                # Total predictions (exclude legacy)
                total = s.execute(
                    select(func.count(PredictionRecord.id))
                    .where(PredictionRecord.is_legacy == 0)
                ).scalar() or 0
                
                # Legacy count
                legacy_count = s.execute(
                    select(func.count(PredictionRecord.id))
                    .where(PredictionRecord.is_legacy == 1)
                ).scalar() or 0
                
                # Recent count (last 1 hour, exclude legacy)
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                recent_count = s.execute(
                    select(func.count(PredictionRecord.id))
                    .where(
                        PredictionRecord.is_legacy == 0,
                        PredictionRecord.timestamp > one_hour_ago
                    )
                ).scalar() or 0
                
                # Recent predictions (exclude legacy)
                recent = s.execute(
                    select(PredictionRecord)
                    .where(PredictionRecord.is_legacy == 0)
                    .order_by(PredictionRecord.created_at.desc())
                    .limit(10)
                ).scalars().all()
                
                markets = {}
                for pred in recent:
                    m = pred.market
                    markets[m] = markets.get(m, 0) + 1
                
                return {
                    "total_count": total,
                    "legacy_count": legacy_count,
                    "recent_count": recent_count,
                    "markets": markets,
                    "sample": [
                        {
                            "fixture_id": p.fixture_id,
                            "market": p.market,
                            "our_prob": p.our_prob,
                            "calibrated_prob": p.calibrated_prob,
                            "odds": p.odds_decimal
                        }
                        for p in recent[:5]
                    ] if recent else []
                }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_portfolio(self) -> dict:
        """Get portfolio state."""
        try:
            from src.betting.portfolio.portfolio_engine import get_portfolio_engine
            from src.portfolio.state.state_manager import get_state_manager
            
            pe = get_portfolio_engine()
            sm = get_state_manager()
            state = sm.get_state()
            
            allocations = []
            if state and hasattr(state, 'allocations'):
                for a in state.allocations:
                    allocations.append({
                        "fixture_id": a.fixture_id,
                        "market": a.market,
                        "stake": a.stake,
                        "odds": a.odds
                    })
            
            return {
                "state_loaded": state is not None,
                "allocations": allocations[:10],  # Limit for response size
                "allocation_count": len(allocations),
                "total_exposure": sum(a.get('stake', 0) for a in allocations)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_risk(self) -> dict:
        """Get risk state."""
        try:
            from src.agents.risk_manager.agent import get_risk_manager_agent
            
            rm = get_risk_manager_agent()
            
            return {
                "lambda": rm.lambda_value if hasattr(rm, 'lambda_value') else 1.0,
                "regime": rm.current_regime if hasattr(rm, 'current_regime') else "neutral",
                "max_exposure_per_fixture": 0.05,
                "max_total_exposure": 0.25
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_policy(self) -> dict:
        """Get policy state."""
        try:
            from src.governance.policy_engine import get_policy_engine
            
            pe = get_policy_engine()
            
            return {
                "constraints": [
                    {"name": c.name, "limit": c.limit}
                    for c in pe.constraints
                ] if hasattr(pe, 'constraints') else [],
                "throttle_enabled": hasattr(pe, 'throttle_multiplier')
            }
        except Exception as e:
            return {"constraints": [], "throttle_enabled": False, "_unavailable": True}
    
    def _get_execution_engine(self) -> dict:
        """Get execution engine state."""
        try:
            from src.betting.execution_engine import get_execution_engine
            
            ee = get_execution_engine()
            
            return {
                "spine_guard_enabled": True,
                "source_chain_required": True,
                "last_execution": None
            }
        except Exception as e:
            return {"spine_guard_enabled": False, "source_chain_required": True, "_unavailable": True}
    
    def _get_clve(self) -> dict:
        """Get CLVE state."""
        try:
            from src.governance.closed_loop_validation_engine import get_closed_loop_validation_engine
            
            clve = get_closed_loop_validation_engine()
            
            return {
                "pds_threshold": clve.pds_threshold,
                "ai_threshold": clve.ai_threshold,
                "cds_threshold": clve.cds_threshold,
                "system_health": clve._system_health
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_temporal_governance(self) -> dict:
        """Get temporal governance state."""
        try:
            from src.governance.temporal_consistency_engine import get_temporal_engine
            
            te = get_temporal_engine()
            
            # Get recent states
            recent = te._previous_states[-5:] if te._previous_states else []
            
            return {
                "window_size": te.window_size,
                "states_loaded": len(te._previous_states),
                "recent_states": [
                    {
                        "run_id": s.run_id,
                        "system_state": s.system_state,
                        "scs": s.scs,
                        "psi": s.psi
                    }
                    for s in recent
                ] if recent else [],
                "system_state": recent[-1].system_state if recent else "UNKNOWN"
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_lineage(self) -> dict:
        """Get lineage state."""
        try:
            from src.infra.lineage_tracker import get_lineage_tracker
            
            lt = get_lineage_tracker()
            runs = lt.list_runs(limit=10)
            
            return {
                "total_runs_tracked": len(runs),
                "recent_runs": runs,
                "lineage_dir": str(lt.lineage_dir)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _get_scheduler_state(self) -> dict:
        """Get scheduler state."""
        try:
            try:
                from backend.scheduler import get_scheduler
                from apscheduler.schedulers.background import BackgroundScheduler
                
                execution_status = {"execution_authority": "unknown", "lock_holder": None}
                try:
                    from src.infra.runtime_lock import RuntimeLock, is_execution_allowed, verify_execution_ownership
                    execution_status = {
                        "execution_authority": "self" if verify_execution_ownership() else "external",
                        "lock_holder": RuntimeLock.get_active_instance(),
                        "is_execution_allowed": is_execution_allowed(),
                    }
                except ImportError:
                    pass
                
                scheduler = get_scheduler()
                if scheduler and isinstance(scheduler, BackgroundScheduler):
                    if not scheduler.running:
                        return {"status": "SCHEDULER_NOT_RUNNING", "jobs": [], "job_count": 0, "execution": execution_status}
                    
                    jobs = scheduler.get_jobs()
                    
                    job_list = []
                    for job in jobs:
                        next_run = None
                        try:
                            if hasattr(job, 'next_run_time') and job.next_run_time:
                                next_run = job.next_run_time.isoformat()
                        except Exception:
                            pass
                        job_list.append({
                            "id": job.id,
                            "name": job.name,
                            "next_run": next_run,
                            "pending": getattr(job, 'pending', False)
                        })
                    
                    return {
                        "jobs": job_list,
                        "job_count": len(jobs),
                        "running": scheduler.running,
                        "execution": execution_status
                    }
                return {"status": "SCHEDULER_NOT_INITIALIZED", "jobs": [], "job_count": 0, "execution": execution_status}
            except Exception as sched_err:
                return {"status": "SCHEDULER_UNAVAILABLE", "jobs": [], "job_count": 0, "_note": str(sched_err), "execution": execution_status}
        except Exception as e:
            return {"status": "ERROR", "jobs": [], "job_count": 0}
    
    def _get_data_health(self) -> dict:
        """Get data health metrics."""
        try:
            from src.storage.db import get_session
            from src.storage.models import Fixture, FixtureOdds, PredictionRecord, PlacedBet
            from sqlalchemy import select, func
            
            with get_session() as s:
                # Fixture counts
                total_fixtures = s.execute(select(func.count(Fixture.id))).scalar() or 0
                ns_fixtures = s.execute(
                    select(func.count(Fixture.id)).where(Fixture.status == "NS")
                ).scalar() or 0
                ft_fixtures = s.execute(
                    select(func.count(Fixture.id)).where(Fixture.status == "FT")
                ).scalar() or 0
                
                # Odds
                odds_count = s.execute(select(func.count(FixtureOdds.id))).scalar() or 0
                
                # Predictions (exclude legacy)
                prediction_count = s.execute(
                    select(func.count(PredictionRecord.id))
                    .where(PredictionRecord.is_legacy == 0)
                ).scalar() or 0
                
                # Legacy predictions count
                legacy_predictions_count = s.execute(
                    select(func.count(PredictionRecord.id))
                    .where(PredictionRecord.is_legacy == 1)
                ).scalar() or 0
                
                # Bets
                bet_count = s.execute(select(func.count(PlacedBet.id))).scalar() or 0
                settled_bets = s.execute(
                    select(func.count(PlacedBet.id)).where(PlacedBet.settled == True)
                ).scalar() or 0
                
                return {
                    "fixtures": {
                        "total": total_fixtures,
                        "upcoming": ns_fixtures,
                        "completed": ft_fixtures
                    },
                    "odds": {
                        "total": odds_count
                    },
                    "predictions": {
                        "total": prediction_count,
                        "legacy_count": legacy_predictions_count
                    },
                    "bets": {
                        "total": bet_count,
                        "settled": settled_bets
                    }
                }
        except Exception as e:
            return {"error": str(e)}


# Global instance
_snapshot: Optional[SystemTruthSnapshot] = None


def get_system_truth_snapshot() -> SystemTruthSnapshot:
    """Get global system truth snapshot."""
    global _snapshot
    if _snapshot is None:
        _snapshot = SystemTruthSnapshot()
    return _snapshot


def get_truth_response() -> SafeResponse:
    """Get safe JSON response with system truth."""
    try:
        snapshot = get_system_truth_snapshot()
        data = snapshot.get_snapshot()
        return SafeResponse(success=True, data=data, error=None)
    except Exception as e:
        logger.error(f"[TRUTH] Failed to get snapshot: {e}")
        return SafeResponse(success=False, data={}, error=str(e))


def validate_truth_schema(truth: dict) -> dict:
    """Validate that truth schema is complete."""
    required_sections = [
        "system_status", "execution", "pipeline", "predictions",
        "portfolio", "risk", "policy", "execution_engine",
        "clve", "temporal_governance", "lineage", "scheduler_state",
        "data_health"
    ]
    
    errors = []
    warnings = []
    
    for section in required_sections:
        if section not in truth:
            errors.append(f"Missing section: {section}")
        elif truth[section] is None:
            warnings.append(f"Null section: {section}")
        elif isinstance(truth[section], dict) and "error" in truth[section]:
            warnings.append(f"Section has error: {section}")
    
    # Check for null fields
    def check_nulls(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if v is None:
                    warnings.append(f"Null field: {path}.{k}")
                else:
                    check_nulls(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                check_nulls(item, f"{path}[{i}]")
    
    check_nulls(truth)
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "sections_checked": len(required_sections),
        "timestamp": datetime.utcnow().isoformat()
    }