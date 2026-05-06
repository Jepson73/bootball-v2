import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def create_app():
    """Create and configure the Flask app.
    
    ENSURES:
    - Runtime mode is loaded first (single source of truth)
    - Scheduler is ALWAYS started on boot (deterministic)
    - AgentCoordinator is the only execution spine
    """
    from backend.config import get_config
    from backend.runtime_mode import (
        RuntimeModeManager, 
        is_live_eval_mode, 
        is_live_mode,
        get_mode_name,
        get_allowed_scheduler_jobs
    )
    from backend.experiment_tracker import ExperimentTracker
    from src.storage.db import init_db
    from scripts import web_ui

    runtime_mgr = RuntimeModeManager()
    mode = runtime_mgr.get_mode_name()
    
    logger.info(f"Initializing Flask app in {mode.upper()} mode")
    
    init_db()
    cfg = get_config()
    web_ui.app.secret_key = cfg.SECRET_KEY
    
    tracker = ExperimentTracker()
    snapshot = tracker.capture_system_snapshot(mode, cfg.MODEL_DIR)
    run_id = tracker.start_run(mode, cfg.MODEL_DIR, record_in_db=(mode != "dev"))
    web_ui.app.config['RUN_ID'] = run_id
    
    logger.info(f"📊 Experiment tracking initialized: run_id={run_id}")
    
    if cfg.SCHEDULER_ENABLED:
        allowed_jobs = get_allowed_scheduler_jobs()
        web_ui.app.scheduler = start_scheduler(allowed_jobs, mode)
        
        if is_live_eval_mode():
            web_ui.app.config['EVAL_MODE_ACTIVE'] = True
            logger.info("🔒 EVAL_MODE_ACTIVE: Flask app is in inference-only mode")
        elif is_live_mode():
            logger.info("🔒 LIVE MODE: Stricter policy constraints enforced")
    else:
        web_ui.app.scheduler = None
        logger.warning("⚠️  Scheduler disabled in config - automated pipeline will NOT run")

    return web_ui.app


def start_scheduler(allowed_jobs: dict = None, mode: str = None):
    """Start the APScheduler - DETERMINISTIC INITIALIZATION.
    
    ENSURES:
    - Scheduler starts exactly once
    - Never relies on manual trigger
    - AgentCoordinator is the single execution entry point
    """
    try:
        from backend.scheduler import start_scheduler as _start
        from backend.runtime_mode import get_mode_name
        
        scheduler = _start()
        
        if mode is None:
            mode = get_mode_name()
        
        if allowed_jobs:
            blocked = [j for j, info in allowed_jobs.items() if not info['allowed']]
            if blocked:
                logger.info(f"Blocked jobs in {mode} mode: {blocked}")
        
        if scheduler and scheduler.running:
            jobs = scheduler.get_jobs()
            job_ids = [j.id for j in jobs]
            logger.info(f"✅ Scheduler started with {len(jobs)} jobs: {job_ids}")
            
            if 'continuous_cycle' in job_ids or 'run_continuous_cycle' in job_ids:
                logger.info("✅ SINGLE EXECUTION SPINE: AgentCoordinator via continuous_cycle")
            else:
                logger.warning("⚠️  continuous_cycle job not found in scheduler")
        else:
            logger.warning("⚠️  Scheduler created but not running")
        
        return scheduler
    except Exception:
        logger.exception("Failed to start scheduler")
        return None


def stop_scheduler(scheduler):
    """Stop the APScheduler."""
    try:
        from backend.scheduler import stop_scheduler as _stop
        _stop(scheduler)
    except Exception:
        logger.exception("Failed to stop scheduler")


if __name__ == "__main__":
    from backend.runtime_mode import is_live_eval_mode, get_mode_name
    from backend.config import get_config
    from backend.experiment_tracker import ExperimentTracker
    
    cfg = get_config()
    mode = get_mode_name()
    
    logger.info("=" * 60)
    logger.info(f"BOOTBALL APPLICATION STARTING")
    logger.info(f"Runtime Mode: {mode.upper()}")
    logger.info(f"Scheduler: {'enabled' if cfg.SCHEDULER_ENABLED else 'disabled'}")
    logger.info("=" * 60)
    
    app = create_app()

    import atexit
    atexit.register(lambda: stop_scheduler(app.scheduler) if hasattr(app, 'scheduler') and app.scheduler else None)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
