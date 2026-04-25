import logging
from typing import Any, Callable

from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class ConsumerRegistry:
    """
    Central registry for event consumers.
    
    Manages registration and dispatch of events to consumers.
    This is the ONLY place where routing logic exists.
    """

    def __init__(self):
        self._consumers: list[EventConsumer] = []
        self._consumer_by_name: dict[str, EventConsumer] = {}
        self._dispatch_handlers: dict[str, Callable] = {}

    def register_consumer(self, consumer: EventConsumer) -> None:
        """
        Register a consumer with the registry.
        
        Args:
            consumer: An EventConsumer instance
        """
        if consumer.name in self._consumer_by_name:
            logger.warning(f"Consumer {consumer.name} already registered, skipping")
            return
            
        self._consumers.append(consumer)
        self._consumer_by_name[consumer.name] = consumer
        logger.info(f"Registered consumer: {consumer.name}")

    def unregister_consumer(self, consumer_name: str) -> None:
        """
        Unregister a consumer by name.
        
        Args:
            consumer_name: Name of the consumer to remove
        """
        if consumer_name in self._consumer_by_name:
            consumer = self._consumer_by_name.pop(consumer_name)
            self._consumers = [c for c in self._consumers if c.name != consumer_name]
            logger.info(f"Unregistered consumer: {consumer_name}")

    def get_consumers_for_event(self, event_type: str) -> list[EventConsumer]:
        """
        Get all consumers that handle a specific event type.
        
        Args:
            event_type: The canonical event type
            
        Returns:
            List of consumers that handle this event
        """
        return [c for c in self._consumers if c.handles(event_type)]

    def dispatch_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """
        Dispatch an event to all registered consumers that handle it.
        
        Args:
            event_type: The canonical event type
            payload: Event payload data
        """
        consumers = self.get_consumers_for_event(event_type)
        
        if not consumers:
            logger.debug(f"No consumers for event: {event_type}")
            return
        
        event = {
            "event_type": event_type,
            "payload": payload,
        }
        
        for consumer in consumers:
            try:
                logger.debug(f"Dispatching {event_type} to {consumer.name}")
                consumer.process(event)
            except Exception as e:
                logger.error(f"Consumer {consumer.name} failed to process {event_type}: {e}")

    def list_consumers(self) -> list[str]:
        """List all registered consumer names."""
        return [c.name for c in self._consumers]


# Global registry instance
registry = ConsumerRegistry()