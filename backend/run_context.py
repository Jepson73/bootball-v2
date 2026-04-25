import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    """
    Mandatory execution context for all prediction and betting operations.
    
    This object MUST be passed through all business logic layers.
    It is created ONLY at entry points (APScheduler jobs, CLI scripts).
    
    Attributes:
        run_id: Unique identifier for this experiment run
        mode: Runtime mode (dev, training, live_eval)
        start_timestamp: When this run started
        is_simulation: Whether this is a simulation (no real bets)
    """
    run_id: str
    mode: str
    start_timestamp: datetime = field(default_factory=datetime.utcnow)
    is_simulation: bool = True
    
    def __post_init__(self):
        if not self.run_id:
            raise ValueError("RunContext requires a non-empty run_id")
        if self.mode not in ['dev', 'training', 'live_eval']:
            raise ValueError(f"Invalid mode: {self.mode}")


def require_run_context(context: Optional[RunContext], operation: str = "operation") -> RunContext:
    """
    Enforce that a RunContext is present.
    
    This is the central enforcement function that MUST be called
    at the start of all prediction, betting, and calibration pipelines.
    
    Args:
        context: The RunContext to validate
        operation: Description of the operation being performed (for error messages)
    
    Returns:
        The validated RunContext
    
    Raises:
        RuntimeError: If context is None or has no run_id
    """
    if context is None:
        raise RuntimeError(
            f"FATAL: {operation} requires RunContext but None was provided. "
            f"Execution cannot proceed without a valid run context."
        )
    
    if not hasattr(context, 'run_id') or not context.run_id:
        raise RuntimeError(
            f"FATAL: {operation} requires RunContext with valid run_id but "
            f"context has no run_id. Execution cannot proceed without a valid run context."
        )
    
    if not hasattr(context, 'mode'):
        raise RuntimeError(
            f"FATAL: {operation} requires RunContext with mode but context has no mode."
        )
    
    logger.debug(f"RunContext validated: run_id={context.run_id}, mode={context.mode}")
    return context


def create_run_context(run_id: str, mode: str, is_simulation: bool = True) -> RunContext:
    """
    Factory function to create a RunContext.
    
    This is the ONLY way to create a RunContext in the entire system.
    All entry points MUST use this function.
    
    Args:
        run_id: The experiment run ID
        mode: Runtime mode (dev, training, live_eval)
        is_simulation: Whether this is a simulation (default: True for safety)
    
    Returns:
        A new RunContext instance
    """
    return RunContext(
        run_id=run_id,
        mode=mode,
        start_timestamp=datetime.utcnow(),
        is_simulation=is_simulation
    )


def get_current_run_context() -> Optional[RunContext]:
    """
    Get the current run context from the global context stack.
    
    Returns None if no context has been pushed.
    """
    return _run_context_stack[-1] if _run_context_stack else None


_run_context_stack: list[RunContext] = []


def push_run_context(context: RunContext) -> None:
    """Push a RunContext onto the global stack."""
    global _run_context_stack
    _run_context_stack.append(context)
    logger.debug(f"Pushed RunContext: {context.run_id}")


def pop_run_context() -> Optional[RunContext]:
    """Pop the current RunContext from the global stack."""
    global _run_context_stack
    if _run_context_stack:
        context = _run_context_stack.pop()
        logger.debug(f"Popped RunContext: {context.run_id}")
        return context
    return None


class RunContextGuard:
    """
    Context manager for automatic RunContext push/pop.
    
    Usage:
        context = create_run_context(run_id, mode)
        with RunContextGuard(context):
            # All business logic here has access to context
            prediction_pipeline(context)
    """
    def __init__(self, context: RunContext):
        self.context = context
    
    def __enter__(self) -> RunContext:
        push_run_context(self.context)
        require_run_context(self.context, "RunContextGuard")
        return self.context
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pop_run_context()
        return False