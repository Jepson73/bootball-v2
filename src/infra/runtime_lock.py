import os
import logging
import fcntl
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_FILE = "/opt/projects/bootball/data/execution_runtime.lock"


class RuntimeLock:
    """Single runtime ownership guard.
    
    Ensures only ONE ExecutionRuntime instance can run in the system.
    Uses file-based locking for cross-process safety.
    """
    
    _active_instance: Optional[str] = None
    _lock_file_handle: Optional[int] = None
    _lock_path: Optional[str] = None

    @classmethod
    def acquire(cls, instance_id: str, lock_file: str = None) -> None:
        """Acquire the runtime lock.

        Args:
            instance_id: Unique identifier for this instance
            lock_file: Override the default lock path. Phase 31: the V2 runtime uses
                its own distinct lock file during the parallel-verification window
                so it can run alongside the still-live V1 runtime without contending
                for the same lock; callers that omit this get the original V1 path.

        Raises:
            RuntimeError: If lock is already held by another instance
        """
        if cls._active_instance is not None:
            raise RuntimeError(
                f"EXECUTION RUNTIME ALREADY ACTIVE: {cls._active_instance}. "
                f"Cannot acquire for {instance_id}"
            )

        try:
            cls._lock_path = lock_file or LOCK_FILE
            lock_path = Path(cls._lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            cls._lock_file_handle = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
            
            fcntl.flock(cls._lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            os.write(cls._lock_file_handle, f"{instance_id}:{datetime.utcnow().isoformat()}".encode())
            
            cls._active_instance = instance_id
            
            logger.info(f"RuntimeLock acquired for: {instance_id}")
        except (IOError, OSError) as e:
            if cls._lock_file_handle is not None:
                try:
                    os.close(cls._lock_file_handle)
                except OSError as close_err:
                    logger.warning("Failed to close lock fd during acquire cleanup: %s", close_err)
            cls._lock_file_handle = None
            
            existing = cls._check_existing(cls._lock_path)
            raise RuntimeError(
                f"Could not acquire lock: {e}. "
                f"Existing holder: {existing or 'unknown'}"
            )

    @classmethod
    def release(cls) -> None:
        """Release the runtime lock."""
        if cls._active_instance is None:
            logger.warning("RuntimeLock.release() called but no active instance")
            return

        logger.info(f"Releasing RuntimeLock: {cls._active_instance}")

        try:
            if cls._lock_file_handle is not None:
                fcntl.flock(cls._lock_file_handle, fcntl.LOCK_UN)
                os.close(cls._lock_file_handle)
                cls._lock_file_handle = None

                try:
                    os.unlink(cls._lock_path or LOCK_FILE)
                except OSError as unlink_err:
                    logger.debug("Could not remove lock file during release (may already be gone): %s", unlink_err)
        except Exception as e:
            logger.warning(f"Error releasing lock: {e}")

        cls._active_instance = None

    @classmethod
    def _check_existing(cls, lock_file: str = None) -> Optional[str]:
        """Check for existing lock holder."""
        try:
            lock_path = Path(lock_file or LOCK_FILE)
            if lock_path.exists():
                with open(lock_path) as f:
                    content = f.read().strip()
                    if content:
                        return content.split(":")[0]
        except OSError as e:
            logger.warning("Failed to read lock file for existing holder check: %s", e)
        return None

    @classmethod
    def is_locked(cls, lock_file: str = None) -> bool:
        """Check if lock is currently held."""
        if cls._active_instance is not None:
            return True

        try:
            lock_path = Path(lock_file or LOCK_FILE)
            if lock_path.exists():
                return True
        except OSError as e:
            logger.warning("Failed to check lock file existence: %s", e)

        return False
    
    @classmethod
    def get_active_instance(cls) -> Optional[str]:
        """Get the current lock holder."""
        return cls._active_instance


def is_execution_allowed() -> bool:
    """Check if execution is allowed.
    
    Returns True if no other execution runtime is running.
    Use this before triggering any execution from non-runtime processes.
    """
    return not RuntimeLock.is_locked()


def verify_execution_ownership() -> bool:
    """Verify that current process owns the execution lock.
    
    Returns True if this process is the valid execution authority.
    """
    return RuntimeLock._active_instance is not None
