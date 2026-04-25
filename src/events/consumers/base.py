from abc import ABC, abstractmethod
from typing import Any


class EventConsumer(ABC):
    """
    Base class for event consumers.
    
    Consumers handle side effects:
    - Discord alerts
    - Dashboard updates
    - Health monitoring
    - Model tracking
    
    They are registered with the EventBus and receive
    events asynchronously after pipelines complete.
    """

    @abstractmethod
    def handles(self, event_type: str) -> bool:
        """
        Check if this consumer handles the given event type.
        
        Args:
            event_type: The canonical event type string
            
        Returns:
            True if this consumer should receive events of this type
        """
        pass

    @abstractmethod
    def process(self, event: dict[str, Any]) -> None:
        """
        Process the event.
        
        This is where side effects happen:
        - Send Discord messages
        - Update dashboard state
        - Log metrics
        - Trigger external systems
        
        Args:
            event: Full event dict with event_type, timestamp, and payload
        """
        pass

    @property
    def name(self) -> str:
        """Consumer name for logging and identification."""
        return self.__class__.__name__


class BatchEventConsumer(EventConsumer):
    """
    Consumer that batches events for efficiency.
    
    Useful for consumers that need to aggregate data
    before sending (e.g., batching Discord notifications).
    """

    def __init__(self, batch_size: int = 10, batch_timeout_seconds: float = 60.0):
        self._batch_size = batch_size
        self._batch_timeout = batch_timeout_seconds
        self._pending_events: list[dict] = []

    def handles(self, event_type: str) -> bool:
        pass

    def process(self, event: dict[str, Any]) -> None:
        self._pending_events.append(event)
        
        if len(self._pending_events) >= self._batch_size:
            self._flush()

    def _flush(self) -> None:
        """Override to implement batch processing logic."""
        pass

    def _clear_batch(self) -> None:
        """Clear the pending events batch."""
        self._pending_events = []