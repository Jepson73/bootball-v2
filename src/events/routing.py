"""
Event routing configuration.

Maps event types to consumer classes that handle them.
"""

EVENT_ROUTING = {
    "run_started": ["HealthDashboardConsumer"],
    "run_finished": [
        "DiscordConsumer",
        "HealthDashboardConsumer",
        "ModelTrendConsumer",
        "BettingDashboardConsumer"
    ],
    "predictions_generated": ["BettingDashboardConsumer"],
    "bets_generated": ["DiscordConsumer", "BettingDashboardConsumer"],
    "bets_settled": ["BettingDashboardConsumer"],
    "bet_settled": ["BettingDashboardConsumer"],
    "health_update": ["HealthDashboardConsumer"],
    "model_trend": ["ModelTrendConsumer"]
}


def get_consumers_for_event(event_type: str) -> list[str]:
    """
    Get list of consumer class names for an event type.
    
    Args:
        event_type: The canonical event type
        
    Returns:
        List of consumer class names that handle this event
    """
    return EVENT_ROUTING.get(event_type, [])


def all_consumer_names() -> list[str]:
    """Get all unique consumer class names defined in routing."""
    names = set()
    for consumers in EVENT_ROUTING.values():
        names.update(consumers)
    return sorted(names)
