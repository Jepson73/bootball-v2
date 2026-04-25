import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger(__name__)

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///data/scheduler.db')
}

MUTATING_JOBS = {"retrain_models", "run_betting_bot"}


def is_job_allowed_in_mode(job_id: str) -> bool:
    """Check if a job is allowed to run in the current runtime mode."""
    from backend.runtime_mode import is_live_eval_mode, is_training_mode
    
    if job_id in MUTATING_JOBS:
        if is_live_eval_mode():
            logger.warning(
                f"⏭️  SCHEDULER SKIP: Job '{job_id}' is a mutating job and is "
                f"BLOCKED in LIVE_EVAL mode for evaluation integrity"
            )
            return False
        elif is_training_mode() and job_id == "run_betting_bot":
            logger.warning(
                f"⏭️  SCHEDULER SKIP: Job '{job_id}' (betting) is "
                f"BLOCKED in TRAINING mode"
            )
            return False
    
    return True


def job_fetch_fixtures():
    """Pull upcoming fixtures, upsert changes."""
    logger.info("JOB: fetch_fixtures starting")
    from backend.execution_engine import get_execution_engine
    engine = get_execution_engine()
    try:
        engine.run_job("fetch_fixtures", None)
        logger.info("JOB: fetch_fixtures completed")
    except Exception as e:
        logger.error(f"JOB: fetch_fixtures failed: {e}")


def job_fetch_results():
    """Update finished match scores and outcomes."""
    logger.info("JOB: fetch_results starting")
    from backend.execution_engine import get_execution_engine
    from src.storage.db import get_session
    from sqlalchemy import text
    
    engine = get_execution_engine()
    fixtures_updated = 0
    success = False
    error_msg = None
    
    try:
        engine.run_job("fetch_results", None)
        success = True
        logger.info(f"JOB: fetch_results completed")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"JOB: fetch_results failed: {e}")
    
    try:
        with get_session() as s:
            s.execute(
                text("""INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message) 
                       VALUES (:job, :success, :updated, :error)"""),
                {"job": "fetch_results", "success": success, "updated": fixtures_updated, "error": error_msg}
            )
            s.commit()
    except Exception as log_err:
        logger.error(f"Failed to log to ingestion_log: {log_err}")


def job_fetch_odds():
    """Pull latest odds into fixture_odds + bookmaker_odds."""
    logger.info("JOB: fetch_odds starting")
    from backend.execution_engine import get_execution_engine
    engine = get_execution_engine()
    try:
        engine.run_job("fetch_odds", None)
        logger.info("JOB: fetch_odds completed")
    except Exception as e:
        logger.error(f"JOB: fetch_odds failed: {e}")


def job_auto_heal_runs():
    """Auto-healing: detect and fix broken runs."""
    logger.info("JOB: auto_heal_runs starting")
    try:
        from backend.auto_healing_engine import run_auto_healing
        result = run_auto_healing()
        logger.info(f"JOB: auto_heal_runs completed - {result.get('healed_count', 0)} runs healed")
    except Exception as e:
        logger.error(f"JOB: auto_heal_runs failed: {e}")


def job_cleanup_matches():
    """Cleanup stale live matches and archive old finished matches."""
    logger.info("JOB: cleanup_matches starting")
    try:
        from backend.match_state_normalizer import cleanup_finished_matches
        result = cleanup_finished_matches()
        logger.info(f"JOB: cleanup_matches completed - {result.get('transitioned_to_ft', 0)} transitioned, {result.get('archived', 0)} archived")
    except Exception as e:
        logger.error(f"JOB: cleanup_matches failed: {e}")


def job_run_predictions():
    """Run ML inference, write to prediction_records."""
    logger.info("JOB: run_predictions starting")
    
    from backend.runtime_mode import get_mode_name
    from backend.run_context import create_run_context
    from backend.experiment_tracker import get_tracker
    from backend.execution_engine import get_execution_engine
    
    mode = get_mode_name()
    tracker = get_tracker()
    engine = get_execution_engine()
    run_id = None
    context = None
    
    try:
        if mode in ['training', 'dev']:
            run_id = tracker.start_run(runtime_mode=mode)
            logger.info(f"JOB: Started experiment run {run_id}")
            context = create_run_context(run_id, mode)
            
            # TEMP DEBUG: fail-fast guard
            if run_id is None:
                raise RuntimeError("CRITICAL: Run ID not created in allowed mode")
        
        engine.run_job("daily_predictions", context)
        
        if run_id:
            tracker.finalize_run(run_id)
            logger.info(f"JOB: Finalized experiment run {run_id}")
        
        logger.info("JOB: run_predictions completed")
    except Exception as e:
        logger.error(f"JOB: run_predictions failed: {e}")
        if run_id:
            try:
                tracker.finalize_run(run_id, status='failed')
            except:
                pass


def job_retrain_models():
    """Full retrain, write model_versions + retrain_events."""
    logger.info("JOB: retrain_models starting")
    
    from backend.runtime_mode import get_mode_name
    from backend.run_context import create_run_context
    from backend.experiment_tracker import get_tracker
    from backend.execution_engine import get_execution_engine
    
    mode = get_mode_name()
    tracker = get_tracker()
    engine = get_execution_engine()
    run_id = None
    context = None
    
    try:
        if mode in ['training', 'dev']:
            run_id = tracker.start_run(runtime_mode=mode)
            logger.info(f"JOB: Started experiment run {run_id}")
            context = create_run_context(run_id, mode)
            
            # TEMP DEBUG: fail-fast guard
            if run_id is None:
                raise RuntimeError("CRITICAL: Run ID not created in allowed mode")
        
        engine.run_job("retrain_models", context)
        
        if run_id:
            tracker.finalize_run(run_id)
            logger.info(f"JOB: Finalized experiment run {run_id}")
        
        logger.info("JOB: retrain_models completed")
    except Exception as e:
        logger.error(f"JOB: retrain_models failed: {e}")
        if run_id:
            try:
                tracker.finalize_run(run_id, status='failed')
            except:
                pass


def job_run_betting_bot():
    """Evaluate value bets, write to placed_bets (fake money)."""
    logger.info("JOB: run_betting_bot starting")
    
    from backend.runtime_mode import get_mode_name
    from backend.run_context import create_run_context
    from backend.experiment_tracker import get_tracker
    from backend.execution_engine import get_execution_engine
    
    mode = get_mode_name()
    tracker = get_tracker()
    engine = get_execution_engine()
    run_id = None
    context = None
    
    try:
        if mode in ['training', 'dev']:
            run_id = tracker.start_run(runtime_mode=mode)
            logger.info(f"JOB: Started experiment run {run_id}")
            context = create_run_context(run_id, mode)
            
            # TEMP DEBUG: fail-fast guard
            if run_id is None:
                raise RuntimeError("CRITICAL: Run ID not created in allowed mode")
        
        engine.run_job("betting_pipeline", context)
        
        if run_id:
            tracker.finalize_run(run_id)
            logger.info(f"JOB: Finalized experiment run {run_id}")
        
        logger.info("JOB: run_betting_bot completed")
    except Exception as e:
        logger.error(f"JOB: run_betting_bot failed: {e}")
        if run_id:
            try:
                tracker.finalize_run(run_id, status='failed')
            except:
                pass


def get_scheduler() -> BackgroundScheduler:
    """Create and configure the scheduler."""
    try:
        from backend.runtime_mode import is_live_eval_mode, get_mode_name
        mode = get_mode_name()
    except Exception:
        mode = "dev"
    
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300
    )
    
    logger.info(f"Configuring scheduler for RUNTIME_MODE: {mode}")

    try:
        if is_live_eval_mode():
            logger.info("⚠️  LIVE_EVAL MODE: Scheduler will run ONLY ingestion jobs")
            logger.info("   - retrain_models: BLOCKED")
            logger.info("   - run_betting_bot: BLOCKED")
    except Exception:
        pass

    jobs_to_add = [
        ("fetch_fixtures", job_fetch_fixtures, 'interval', {'hours': 6}),
        ("fetch_results", job_fetch_results, 'interval', {'hours': 1}),
        ("fetch_odds", job_fetch_odds, 'interval', {'hours': 1}),
        ("run_predictions", job_run_predictions, 'cron', {'hour': 3, 'minute': 0}),
        ("retrain_models", job_retrain_models, 'cron', {'day_of_week': 'mon', 'hour': 4, 'minute': 0}),
        ("run_betting_bot", job_run_betting_bot, 'interval', {'minutes': 30}),
        ("auto_heal_runs", job_auto_heal_runs, 'interval', {'minutes': 30}),
        ("cleanup_matches", job_cleanup_matches, 'interval', {'minutes': 5}),
    ]
    
    for job_id, job_func, trigger_type, trigger_args in jobs_to_add:
        if not is_job_allowed_in_mode(job_id):
            logger.info(f"   → Skipping job: {job_id}")
            continue
            
        scheduler.add_job(
            job_func,
            trigger_type,
            id=job_id,
            name=f"{job_id} ({mode})",
            replace_existing=True,
            **trigger_args
        )
        logger.info(f"   → Added job: {job_id}")

    return scheduler


def start_scheduler():
    """Start the scheduler."""
    scheduler = get_scheduler()
    scheduler.start()
    logger.info(f"Scheduler started with jobs: {[j.id for j in scheduler.get_jobs()]}")
    return scheduler


def stop_scheduler(scheduler):
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped")