import os
import logging
from enum import Enum
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)


class RuntimeMode(Enum):
    LIVE_EVAL = "live_eval"
    TRAINING = "training"
    DEV = "dev"


class RuntimeModeManager:
    """Central manager for runtime mode enforcement."""
    
    _instance = None
    _mode: RuntimeMode = RuntimeMode.DEV
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._load_mode()
            self._initialized = True
    
    def _load_mode(self):
        mode_str = os.getenv("RUNTIME_MODE", "dev").lower()
        
        try:
            self._mode = RuntimeMode(mode_str)
        except ValueError:
            logger.warning(f"Unknown RUNTIME_MODE '{mode_str}', defaulting to DEV")
            self._mode = RuntimeMode.DEV
        
        logger.info(f"=" * 50)
        logger.info(f"RUNTIME MODE: {self._mode.value.upper()}")
        logger.info(f"=" * 50)
        
        if self._mode == RuntimeMode.LIVE_EVAL:
            logger.info("⚠️  LIVE_EVAL MODE: System is frozen for evaluation")
            logger.info("   - Models loaded once at startup, no hot-swap")
            logger.info("   - Calibration retraining disabled")
            logger.info("   - Model mutations blocked")
            logger.info("   - Only prediction + logging allowed")
        elif self._mode == RuntimeMode.TRAINING:
            logger.info("🔧 TRAINING MODE: Model updates allowed")
        elif self._mode == RuntimeMode.DEV:
            logger.info("🛠️  DEV MODE: Full flexibility enabled")
    
    @property
    def mode(self) -> RuntimeMode:
        return self._mode
    
    @property
    def is_live_eval(self) -> bool:
        return self._mode == RuntimeMode.LIVE_EVAL
    
    @property
    def is_training(self) -> bool:
        return self._mode == RuntimeMode.TRAINING
    
    @property
    def is_dev(self) -> bool:
        return self._mode == RuntimeMode.DEV
    
    def get_mode_name(self) -> str:
        return self._mode.value


def get_runtime_mode() -> RuntimeMode:
    """Get the current runtime mode."""
    return RuntimeModeManager().mode


def get_mode_name() -> str:
    """Get the current runtime mode name as string."""
    return RuntimeModeManager().get_mode_name()


def is_live_eval_mode() -> bool:
    """Check if running in LIVE_EVAL mode."""
    return RuntimeModeManager().is_live_eval


def is_training_mode() -> bool:
    """Check if running in TRAINING mode."""
    return RuntimeModeManager().is_training


def is_dev_mode() -> bool:
    """Check if running in DEV mode."""
    return RuntimeModeManager().is_dev


def assert_mode_allowed(operation: str, allowed_modes: list = None) -> None:
    """
    Guard function that blocks operations based on runtime mode.
    
    Args:
        operation: Description of the operation being attempted
        allowed_modes: List of RuntimeMode enums that are allowed
        
    Raises:
        RuntimeError: If operation is not allowed in current mode
    """
    if allowed_modes is None:
        allowed_modes = [RuntimeMode.TRAINING, RuntimeMode.DEV]
    
    current_mode = RuntimeModeManager().mode
    
    if current_mode not in allowed_modes:
        error_msg = (
            f"BLOCKED: Operation '{operation}' is not allowed in {current_mode.value.upper()} mode. "
            f"Allowed modes: {[m.value for m in allowed_modes]}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    logger.info(f"ALLOWED: {operation} in {current_mode.value.upper()} mode")


def require_live_eval(operation: str) -> None:
    """Require LIVE_EVAL mode for an operation."""
    assert_mode_allowed(operation, [RuntimeMode.LIVE_EVAL])


def require_training_or_dev(operation: str) -> None:
    """Require TRAINING or DEV mode for an operation."""
    assert_mode_allowed(operation, [RuntimeMode.TRAINING, RuntimeMode.DEV])


def block_in_live_eval(operation: str) -> None:
    """Block an operation in LIVE_EVAL mode."""
    if is_live_eval_mode():
        error_msg = f"BLOCKED: '{operation}' is disabled in LIVE_EVAL mode"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


def mode_guard(allowed_modes: list = None):
    """
    Decorator to guard functions by runtime mode.
    
    Usage:
        @mode_guard([RuntimeMode.TRAINING, RuntimeMode.DEV])
        def retrain_models():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            assert_mode_allowed(func.__name__, allowed_modes)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def log_mode_block(operation: str) -> None:
    """Log a blocked operation in LIVE_EVAL mode."""
    logger.warning(
        f"🔒 BLOCKED in LIVE_EVAL: {operation} "
        f"(operation skipped, evaluation integrity maintained)"
    )


def log_scheduler_skip(job_name: str, reason: str = "mutating") -> None:
    """Log a scheduler job skip due to mode restriction."""
    if is_live_eval_mode():
        logger.warning(
            f"⏭️  SCHEDULER SKIP [{job_name}]: {reason} job blocked in LIVE_EVAL mode"
        )


def get_allowed_scheduler_jobs() -> dict:
    """Get dictionary of jobs and whether they're allowed in current mode."""
    
    jobs = {
        "fetch_fixtures": {"allowed": True, "mutating": False, "description": "Data ingestion"},
        "fetch_results": {"allowed": True, "mutating": False, "description": "Score updates"},
        "fetch_odds": {"allowed": True, "mutating": False, "description": "Odds polling"},
        "run_predictions": {"allowed": True, "mutating": False, "description": "ML inference"},
        "retrain_models": {"allowed": True, "mutating": True, "description": "Model retraining"},
        "run_betting_bot": {"allowed": True, "mutating": True, "description": "Bet placement"},
    }
    
    current_mode = RuntimeModeManager().mode
    
    if current_mode == RuntimeMode.LIVE_EVAL:
        for job_id, info in jobs.items():
            if info["mutating"]:
                jobs[job_id]["allowed"] = False
                log_scheduler_skip(job_id, "mutating")
    elif current_mode == RuntimeMode.TRAINING:
        jobs["run_betting_bot"]["allowed"] = False
        log_scheduler_skip("run_betting_bot", "betting disabled in training")
    elif current_mode == RuntimeMode.DEV:
        pass
    
    return jobs


def is_job_allowed(job_id: str) -> bool:
    """Check if a scheduler job is allowed in the current mode."""
    jobs = get_allowed_scheduler_jobs()
    return jobs.get(job_id, {}).get("allowed", True)


def check_operation_allowed(operation: str) -> bool:
    """Check if an operation is allowed without raising an exception."""
    current_mode = RuntimeModeManager().mode
    
    mutating_operations = [
        "retrain",
        "calibrate",
        "update_model",
        "hot_swap",
        "schema_migration",
        "create_index",
        "alter_table",
    ]
    
    if current_mode == RuntimeMode.LIVE_EVAL:
        return operation.lower() not in mutating_operations
    
    return True