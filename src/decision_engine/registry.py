"""
Action Registry - Track action registrations and execution history.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    """Record of an executed action."""
    action_type: str
    payload: dict
    executed_at: datetime
    success: bool
    error: Optional[str] = None


class ActionRegistry:
    """Registry for tracking action executions."""

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._history: list[ActionRecord] = []
        self._action_counts: dict[str, int] = {}

    def record(self, action_type: str, payload: dict, success: bool = True, error: str = None) -> None:
        """Record an action execution."""
        record = ActionRecord(
            action_type=action_type,
            payload=payload,
            executed_at=datetime.utcnow(),
            success=success,
            error=error
        )
        
        self._history.append(record)
        self._action_counts[action_type] = self._action_counts.get(action_type, 0) + 1
        
        # Trim history
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]
        
        logger.debug(f"Recorded action: {action_type}, success={success}")

    def get_history(self, limit: int = 50) -> list[dict]:
        """Get action history."""
        return [
            {
                "action_type": r.action_type,
                "payload": r.payload,
                "executed_at": r.executed_at.isoformat(),
                "success": r.success,
                "error": r.error
            }
            for r in self._history[-limit:]
        ]

    def get_counts(self) -> dict[str, int]:
        """Get action type counts."""
        return self._action_counts.copy()

    def get_last_action(self, action_type: str) -> Optional[ActionRecord]:
        """Get last action of a specific type."""
        for record in reversed(self._history):
            if record.action_type == action_type:
                return record
        return None


# Global registry
_registry: ActionRegistry = None


def get_action_registry() -> ActionRegistry:
    """Get global action registry."""
    global _registry
    if _registry is None:
        _registry = ActionRegistry()
    return _registry
