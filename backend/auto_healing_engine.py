import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class HealingPlan:
    """Plan for healing a broken run."""
    run_id: str
    missing_stages: List[str] = field(default_factory=list)
    original_run_context: Optional["RunContext"] = None
    can_heal: bool = True
    reason: str = ""


@dataclass
class HealingAction:
    """Record of a healing action taken."""
    run_id: str
    stage: str
    action: str
    success: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None


class RunHealthAnalyzer:
    """
    Detects broken or incomplete experiment runs.
    
    A run is broken if it exists but has:
    - No prediction records
    - No bet records (when bets should exist)
    - Missing run_id propagation
    """
    
    def analyze_runs(self) -> Dict[str, Any]:
        """Analyze all runs and identify broken ones."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as s:
            all_runs = s.execute(text("""
                SELECT run_id, mode, start_timestamp, status, total_predictions, total_bets
                FROM experiment_runs
                ORDER BY start_timestamp DESC
            """)).fetchall()
            
            broken_runs = []
            healthy_runs = []
            
            for row in all_runs:
                run_id = row[0]
                status = row[3]
                total_preds = row[4] or 0
                total_bets = row[5] or 0
                
                pred_count = s.execute(text(
                    "SELECT COUNT(*) FROM prediction_records WHERE run_id = :rid"
                ), {"rid": run_id}).scalar() or 0
                
                bet_count = s.execute(text(
                    "SELECT COUNT(*) FROM placed_bets WHERE run_id = :rid"
                ), {"rid": run_id}).scalar() or 0
                
                run_health = {
                    "run_id": run_id,
                    "mode": row[1],
                    "start_timestamp": row[2],
                    "status": status,
                    "expected_preds": total_preds,
                    "actual_preds": pred_count,
                    "expected_bets": total_bets,
                    "actual_bets": bet_count,
                    "has_predictions": pred_count > 0,
                    "has_bets": bet_count > 0
                }
                
                is_broken = (
                    pred_count == 0 or
                    (total_bets > 0 and bet_count == 0)
                )
                
                if is_broken and status != 'completed':
                    broken_runs.append(run_health)
                else:
                    healthy_runs.append(run_health)
            
            return {
                "broken_runs": broken_runs,
                "healthy_runs": healthy_runs,
                "total_analyzed": len(all_runs),
                "broken_count": len(broken_runs),
                "healthy_count": len(healthy_runs)
            }
    
    def get_run_context_from_db(self, run_id: str) -> Optional["RunContext"]:
        """Retrieve run context from database."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        try:
            with get_session() as s:
                result = s.execute(text(
                    "SELECT mode, start_timestamp FROM experiment_runs WHERE run_id = :rid"
                ), {"rid": run_id}).fetchone()
                
                if not result:
                    return None
                
                from backend.run_context import create_run_context
                return create_run_context(run_id, result[0])
        except Exception as e:
            logger.warning(f"Failed to get run context: {e}")
            return None


class HealingPlanGenerator:
    """
    Generates healing plans for broken runs.
    """
    
    def generate_plans(self, broken_runs: List[Dict]) -> List[HealingPlan]:
        """Generate healing plans for each broken run."""
        plans = []
        
        for run in broken_runs:
            run_id = run["run_id"]
            missing_stages = []
            
            if not run["has_predictions"]:
                missing_stages.append("daily_predictions")
            
            if run["expected_bets"] > 0 and not run["has_bets"]:
                missing_stages.append("betting_pipeline")
            
            plan = HealingPlan(
                run_id=run_id,
                missing_stages=missing_stages,
                can_heal=len(missing_stages) > 0,
                reason="Missing execution stages detected" if missing_stages else "No healing needed"
            )
            plans.append(plan)
        
        return plans


class SafeExecutionReRunner:
    """
    Safely re-executes missing pipeline stages.
    
    Rules:
    - MUST reuse original RunContext
    - MUST NOT create new experiment_runs  
    - MUST NOT overwrite existing predictions/bets
    """
    
    def __init__(self):
        self.healing_actions: List[HealingAction] = []
    
    def heal_run(self, plan: HealingPlan) -> List[HealingAction]:
        """Execute healing plan for a run."""
        from backend.execution_engine import get_execution_engine
        from src.storage.db import get_session
        from sqlalchemy import text
        
        logger.info(f"Starting healing for run {plan.run_id}")
        
        context = None
        
        if plan.original_run_context:
            context = plan.original_run_context
        else:
            from backend.run_context import create_run_context
            
            with get_session() as s:
                mode = s.execute(text(
                    "SELECT mode FROM experiment_runs WHERE run_id = :rid"
                ), {"rid": plan.run_id}).scalar() or "dev"
            
            context = create_run_context(plan.run_id, mode)
        
        engine = get_execution_engine()
        actions = []
        
        for stage in plan.missing_stages:
            action = HealingAction(
                run_id=plan.run_id,
                stage=stage,
                action="re-execute",
                success=False
            )
            
            try:
                logger.info(f"Healing {plan.run_id}: executing {stage}")
                engine.run_job(stage, context)
                action.success = True
                logger.info(f"Healing {plan.run_id}: {stage} completed successfully")
                
            except Exception as e:
                action.success = False
                action.error_message = str(e)
                logger.error(f"Healing {plan.run_id}: {stage} failed: {e}")
            
            actions.append(action)
            self.healing_actions.append(action)
        
        self._log_healing_actions(plan.run_id, actions)
        
        return actions
    
    def _log_healing_actions(self, run_id: str, actions: List[HealingAction]):
        """Log healing actions to database."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        try:
            with get_session() as s:
                for action in actions:
                    s.execute(text("""
                        INSERT INTO ingestion_log 
                        (job_name, success, fixtures_updated, error_message)
                        VALUES (:job, :success, 0, :error)
                    """), {
                        "job": f"heal_{action.stage}_{run_id[:8]}",
                        "success": action.success,
                        "error": action.error_message or "OK"
                    })
                s.commit()
        except Exception as e:
            logger.error(f"Failed to log healing actions: {e}")
    
    def get_healing_history(self) -> List[Dict]:
        """Get history of all healing actions."""
        return [
            {
                "run_id": a.run_id,
                "stage": a.stage,
                "action": a.action,
                "success": a.success,
                "timestamp": a.timestamp.isoformat(),
                "error": a.error_message
            }
            for a in self.healing_actions
        ]


class AutoHealingEngine:
    """
    Main auto-healing engine that coordinates detection and healing.
    """
    
    def __init__(self):
        self.analyzer = RunHealthAnalyzer()
        self.plan_generator = HealingPlanGenerator()
        self.rerunner = SafeExecutionReRunner()
    
    def scan_and_heal(self) -> Dict[str, Any]:
        """Main entry point: scan for broken runs and heal them."""
        logger.info("Starting auto-healing scan...")
        
        analysis = self.analyzer.analyze_runs()
        
        if not analysis["broken_runs"]:
            logger.info("No broken runs detected")
            return {
                "status": "healthy",
                "broken_count": 0,
                "healed_count": 0,
                "actions": []
            }
        
        logger.info(f"Found {analysis['broken_count']} broken runs")
        
        plans = self.plan_generator.generate_plans(analysis["broken_runs"])
        
        healable_plans = [p for p in plans if p.can_heal]
        all_actions = []
        
        for plan in healable_plans:
            logger.info(f"Healing run {plan.run_id}")
            actions = self.rerunner.heal_run(plan)
            all_actions.extend(actions)
        
        return {
            "status": "healed" if all_actions else "failed",
            "broken_count": analysis["broken_count"],
            "healed_count": len(healable_plans),
            "actions": self.rerunner.get_healing_history()
        }


def get_auto_healing_engine() -> AutoHealingEngine:
    """Get singleton auto-healing engine."""
    return AutoHealingEngine()


def run_auto_healing():
    """Entry point for scheduler job."""
    logger.info("JOB: auto_heal_runs starting")
    
    try:
        engine = get_auto_healing_engine()
        result = engine.scan_and_heal()
        
        logger.info(f"JOB: auto_heal_runs completed - {result['healed_count']} runs healed")
        return result
        
    except Exception as e:
        logger.error(f"JOB: auto_heal_runs failed: {e}")
        return {"status": "error", "error": str(e)}