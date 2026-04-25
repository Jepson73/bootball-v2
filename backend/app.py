import logging
import os
import sys

sys.path.insert(0, '/opt/projects/bootball')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def create_app():
    """Create and configure the Flask app."""
    from backend.config import get_config
    from backend.runtime_mode import (
        RuntimeModeManager, 
        is_live_eval_mode, 
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
        web_ui.app.scheduler = start_scheduler(allowed_jobs)
        
        if is_live_eval_mode():
            web_ui.app.config['EVAL_MODE_ACTIVE'] = True
            logger.info("🔒 EVAL_MODE_ACTIVE: Flask app is in inference-only mode")
    else:
        web_ui.app.scheduler = None
        logger.info("Scheduler disabled in config")

    return web_ui.app


def start_scheduler(allowed_jobs: dict = None):
    """Start the APScheduler."""
    try:
        from backend.scheduler import start_scheduler as _start
        scheduler = _start()
        
        mode = get_mode_name()
        if allowed_jobs:
            blocked = [j for j, info in allowed_jobs.items() if not info['allowed']]
            if blocked:
                logger.info(f"Blocked jobs in {mode} mode: {blocked}")
        
        logger.info("Scheduler started successfully")
        return scheduler
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        return None


def stop_scheduler(scheduler):
    """Stop the APScheduler."""
    try:
        from backend.scheduler import stop_scheduler as _stop
        _stop(scheduler)
    except Exception as e:
        logger.error(f"Failed to stop scheduler: {e}")


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
