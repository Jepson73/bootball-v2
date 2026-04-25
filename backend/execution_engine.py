import logging
from datetime import datetime
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class JobType(Enum):
    DAILY_PREDICTIONS = "daily_predictions"
    BETTING_PIPELINE = "betting_pipeline"
    RETRAIN_MODELS = "retrain_models"
    CALIBRATION_UPDATE = "calibration_update"
    FETCH_FIXTURES = "fetch_fixtures"
    FETCH_RESULTS = "fetch_results"
    FETCH_ODDS = "fetch_odds"


class ExecutionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ExecutionLog:
    """Record of a single execution."""
    id: Optional[int] = None
    job_name: str = ""
    run_id: str = ""
    context_mode: str = ""
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    status: str = "pending"
    error_message: Optional[str] = None
    result_summary: Optional[str] = None


class ExecutionEngine:
    """
    Central Execution Dispatcher - the ONLY valid entry point for running pipelines.
    
    All pipeline execution must go through this engine. Direct invocation
    of pipeline functions is forbidden and will raise RuntimeError.
    """
    
    _is_dispatching: bool = False
    _current_job: Optional[str] = None
    _instance: Optional["ExecutionEngine"] = None
    
    def __init__(self):
        if ExecutionEngine._instance is not None:
            raise RuntimeError("ExecutionEngine is a singleton. Use get_engine().")
        ExecutionEngine._instance = self
        self._job_handlers: Dict[JobType, Callable] = {}
        self._register_jobs()
    
    @classmethod
    def get_engine(cls) -> "ExecutionEngine":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def is_dispatching(cls) -> bool:
        """Check if execution is currently happening through the engine."""
        return cls._is_dispatching
    
    @classmethod
    def get_current_job(cls) -> Optional[str]:
        """Get the name of the job currently being executed."""
        return cls._current_job
    
    def _register_jobs(self):
        """Register all job handlers."""
        from scripts.daily_run import DailyPipeline
        from scripts.auto_bet import run_pipeline
        
        self._job_handlers[JobType.DAILY_PREDICTIONS] = self._run_daily_predictions
        self._job_handlers[JobType.BETTING_PIPELINE] = self._run_betting_pipeline
        self._job_handlers[JobType.RETRAIN_MODELS] = self._run_retrain_models
        self._job_handlers[JobType.CALIBRATION_UPDATE] = self._run_calibration_update
        self._job_handlers[JobType.FETCH_FIXTURES] = self._run_fetch_fixtures
        self._job_handlers[JobType.FETCH_RESULTS] = self._run_fetch_results
        self._job_handlers[JobType.FETCH_ODDS] = self._run_fetch_odds
    
    def _run_daily_predictions(self, context: "RunContext") -> Dict[str, Any]:
        """Execute daily predictions pipeline."""
        from scripts.daily_run import DailyPipeline
        
        pipeline = DailyPipeline(context=context)
        pipeline.run()
        return {"predictions_completed": True}
    
    def _run_betting_pipeline(self, context: "RunContext") -> Dict[str, Any]:
        """Execute betting pipeline."""
        from scripts.auto_bet import run_pipeline
        
        run_pipeline(context=context)
        return {"betting_completed": True}
    
    def _run_retrain_models(self, context: "RunContext") -> Dict[str, Any]:
        """Execute model retraining."""
        from scripts.train_multi_calibrated import main as train_main
        
        train_main()
        return {"retraining_completed": True}
    
    def _run_calibration_update(self, context: "RunContext") -> Dict[str, Any]:
        """Execute calibration update."""
        logger.info("Calibration update job executed via ExecutionEngine")
        return {"calibration_completed": True}
    
    def _run_fetch_fixtures(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch fixtures from API."""
        from scripts.daily_run import DailyPipeline
        
        pipeline = DailyPipeline(context=context, send_alerts=False)
        pipeline.run()
        return {"fixtures_fetched": True}
    
    def _run_fetch_results(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch match results from API."""
        from scripts.daily_run import DailyPipeline
        
        pipeline = DailyPipeline(context=context, send_alerts=False)
        pipeline.run()
        return {"results_fetched": True}
    
    def _run_fetch_odds(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch odds from API."""
        from scripts.daily_run import DailyPipeline
        
        pipeline = DailyPipeline(context=context, send_alerts=False)
        pipeline.run()
        return {"odds_fetched": True}
    
    def run_job(self, job_name: str, context: "RunContext") -> Dict[str, Any]:
        """
        Execute a job through the central dispatcher.
        
        This is the ONLY valid entry point for running any pipeline.
        
        Args:
            job_name: Name of the job to execute
            context: RunContext with run_id and mode
            
        Returns:
            Dict with execution results
            
        Raises:
            RuntimeError: If execution fails
        """
        from backend.run_context import require_run_context, RunContextGuard
        
        require_run_context(context, f"ExecutionEngine.run_job({job_name})")
        
        job_type = JobType(job_name)
        if job_type not in self._job_handlers:
            raise ValueError(f"Unknown job: {job_name}")
        
        exec_log = ExecutionLog(
            job_name=job_name,
            run_id=context.run_id,
            context_mode=context.mode,
            start_time=datetime.utcnow(),
            status="running"
        )
        
        self._log_execution(exec_log)
        
        ExecutionEngine._is_dispatching = True
        ExecutionEngine._current_job = job_name
        
        logger.info(f"ExecutionEngine: Starting job={job_name}, run_id={context.run_id}, mode={context.mode}")
        
        try:
            with RunContextGuard(context):
                result = self._job_handlers[job_type](context)
            
            exec_log.status = "success"
            exec_log.end_time = datetime.utcnow()
            logger.info(f"ExecutionEngine: Completed job={job_name}, run_id={context.run_id}")
            
            return result
            
        except Exception as e:
            exec_log.status = "failed"
            exec_log.error_message = str(e)
            exec_log.end_time = datetime.utcnow()
            logger.error(f"ExecutionEngine: Failed job={job_name}, run_id={context.run_id}, error={e}")
            raise
            
        finally:
            ExecutionEngine._is_dispatching = False
            ExecutionEngine._current_job = None
            self._log_execution(exec_log)
    
    def _log_execution(self, log: ExecutionLog):
        """Log execution to database."""
        from src.storage.db import get_session
        from sqlalchemy import text
        from datetime import datetime
        
        try:
            with get_session() as sess:
                sess.execute(text("""
                    INSERT INTO execution_logs 
                    (job_name, run_id, context_mode, start_time, end_time, status, error_message, result_summary)
                    VALUES (:job, :run_id, :mode, :start, :end, :status, :error, :result)
                """), {
                    'job': log.job_name,
                    'run_id': log.run_id,
                    'mode': log.context_mode,
                    'start': log.start_time.isoformat() if log.start_time else None,
                    'end': log.end_time.isoformat() if log.end_time else None,
                    'status': log.status,
                    'error': log.error_message,
                    'result': log.result_summary
                })
                sess.commit()
        except Exception as e:
            logger.warning(f"Failed to log execution: {e}")


def get_execution_engine() -> ExecutionEngine:
    """Get the singleton ExecutionEngine instance."""
    return ExecutionEngine.get_engine()


def enforce_execution_boundary():
    """
    Call this at the start of any pipeline function to enforce execution boundaries.
    
    Raises RuntimeError if called outside of ExecutionEngine dispatch.
    """
    if not ExecutionEngine.is_dispatching():
        raise RuntimeError(
            "DIRECT PIPELINE EXECUTION IS FORBIDDEN.\n"
            "All pipeline execution MUST go through ExecutionEngine.run_job().\n"
            "Use ExecutionEngine to dispatch jobs - do not call pipeline functions directly."
        )


def get_current_execution_info() -> Dict[str, str]:
    """Get information about the current execution context."""
    return {
        "is_dispatching": str(ExecutionEngine.is_dispatching()),
        "current_job": ExecutionEngine.get_current_job() or "none"
    }