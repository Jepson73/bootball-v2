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
        from scripts.make_predictions import find_fixtures_needing_predictions, make_predictions_for_fixture
        from src.storage.db import get_session

        total = 0
        with get_session() as s:
            fixture_ids = find_fixtures_needing_predictions(s)

        for fix_id in fixture_ids[:100]:
            with get_session() as s:
                count = make_predictions_for_fixture(s, fix_id, dry_run=False, context=context)
                s.commit()
                total += count

        return {"predictions_completed": True, "predictions_made": total, "fixtures_processed": len(fixture_ids[:100])}
    
    def _run_betting_pipeline(self, context: "RunContext") -> Dict[str, Any]:
        """Execute betting pipeline."""
        from scripts.auto_bet import run_pipeline
        
        run_pipeline(context=context)
        return {"betting_completed": True}
    
    def _run_retrain_models(self, context: "RunContext") -> Dict[str, Any]:
        """Execute model retraining via versioned registry (records in model_versions table)."""
        from scripts.web_ui import _train_market_with_calibration

        results = {}
        markets = ["h2h", "btts", "ou25", "ou15"]
        for market in markets:
            try:
                result = _train_market_with_calibration(market, reason="scheduled")
                results[market] = result
                logger.info("Retrain %s: %s", market, result)
            except Exception:
                logger.exception("Retrain failed for market %s", market)
                results[market] = {"error": "training failed"}

        return {"retraining_completed": True, "results": results}

    def _run_calibration_update(self, context: "RunContext") -> Dict[str, Any]:
        """Execute recalibration of all markets (increments cYY, keeps vXX)."""
        from src.models.model_registry import get_model_registry

        registry = get_model_registry()
        results = {}
        for market in ["h2h", "btts", "ou25", "ou15"]:
            try:
                active = registry.get_active(market)
                if active is None:
                    results[market] = {"error": "no active version to recalibrate"}
                    continue
                # Recalibration refits the calibrator on recent settled predictions.
                calibrator, cal_metrics = _fit_calibrator_for_market(market)
                if calibrator is None:
                    results[market] = {"error": "insufficient settled data for calibration"}
                    continue
                new_ver = registry.register_recalibration(market, calibrator, metrics=cal_metrics, reason="scheduled")
                results[market] = {"label": new_ver["version_label"] if new_ver else None}
            except Exception:
                logger.exception("Recalibration failed for market %s", market)
                results[market] = {"error": "recalibration failed"}

        return {"calibration_completed": True, "results": results}
    
    def _run_fetch_fixtures(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch fixtures from API."""
        from scripts.daily_run import DailyBaselinePipeline

        pipeline = DailyBaselinePipeline(context={"run_id": context.run_id})
        pipeline.run()
        return {"fixtures_fetched": True}

    def _run_fetch_results(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch match results from API."""
        from scripts.daily_run import DailyBaselinePipeline

        pipeline = DailyBaselinePipeline(context={"run_id": context.run_id})
        pipeline.run()
        return {"results_fetched": True}

    def _run_fetch_odds(self, context: "RunContext") -> Dict[str, Any]:
        """Fetch odds from API."""
        from scripts.odds_poll import find_fixtures_needing_odds, poll_and_update_odds, recalculate_prediction_ev
        from src.storage.db import get_session
        from src.ingestion.client import APIFootballClient, calls_remaining_today

        remaining = calls_remaining_today()
        if remaining < 50:
            return {"odds_fetched": False, "reason": "low_api_calls"}

        client = APIFootballClient()
        with get_session() as s:
            fixture_ids = find_fixtures_needing_odds(s)
            updated = poll_and_update_odds(s, client, fixture_ids[:50])
            recalculate_prediction_ev(s, fixture_ids[:50])
        return {"odds_fetched": True, "fixtures_updated": updated}
    
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
            logger.exception("ExecutionEngine: Failed job=%s run_id=%s", job_name, context.run_id)
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


def _fit_calibrator_for_market(market: str):
    """Fit an isotonic calibrator on recent settled prediction_records for a market.

    Returns (calibrator, metrics) tuple, or (None, None) if insufficient data.
    metrics includes brier_score and ece computed on post-calibration probabilities.
    """
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from src.storage.db import get_session
    from sqlalchemy import text

    try:
        with get_session() as s:
            # Use our_prob (raw model output) not calibrated_prob — fitting on
            # already-calibrated values creates a circular dependency that degrades quality.
            rows = s.execute(text("""
                SELECT our_prob, won FROM prediction_records
                WHERE market = :market AND settled = 1 AND our_prob IS NOT NULL AND won IS NOT NULL
                ORDER BY id DESC LIMIT 2000
            """), {"market": market}).fetchall()

        if len(rows) < 100:
            logger.warning("_fit_calibrator: only %d settled rows for %s", len(rows), market)
            return None, None

        probs = np.array([r[0] for r in rows], dtype=float)
        outcomes = np.array([int(r[1]) for r in rows], dtype=float)

        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(probs, outcomes)

        cal_probs = np.clip(calibrator.predict(probs), 0.01, 0.99)
        brier = float(np.mean((cal_probs - outcomes) ** 2))

        n_bins = 10
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (cal_probs >= bin_edges[i]) & (
                cal_probs <= bin_edges[i + 1] if i == n_bins - 1 else cal_probs < bin_edges[i + 1]
            )
            if np.sum(mask) == 0:
                continue
            ece += (np.sum(mask) / len(cal_probs)) * abs(np.mean(outcomes[mask]) - np.mean(cal_probs[mask]))

        metrics = {
            "brier_score": brier,
            "ece": ece,
            "calibration_sample_size": len(rows),
        }
        return calibrator, metrics
    except Exception:
        logger.exception("_fit_calibrator failed for %s", market)
        return None, None


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