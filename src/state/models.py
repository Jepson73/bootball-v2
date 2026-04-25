from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass
class BettingState:
    """
    Betting dashboard state - reconstructed from events.
    """
    balance: float = 0.0
    roi: float = 0.0
    pending_count: int = 0
    wins: int = 0
    losses: int = 0
    pending_stake: float = 0.0
    total_pnl: float = 0.0
    bets: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    active_round_id: Optional[int] = None
    active_round_number: Optional[int] = None
    initial_bankroll: float = 1000.0


@dataclass
class HealthState:
    """
    Health dashboard state - reconstructed from events.
    """
    active_runs: list[dict] = field(default_factory=list)
    completed_runs: list[dict] = field(default_factory=list)
    health_score: float = 100.0
    error_rate: float = 0.0
    avg_duration: float = 0.0
    total_runs: int = 0
    failed_runs: int = 0
    last_updated: Optional[datetime] = None


@dataclass
class ModelState:
    """
    Model performance state - reconstructed from events.
    """
    model_versions: list[dict] = field(default_factory=list)
    market_performance: dict[str, list[dict]] = field(default_factory=dict)
    calibration_drift: dict[str, list[dict]] = field(default_factory=dict)
    roi_by_model: dict[str, float] = field(default_factory=dict)
    active_versions: list[str] = field(default_factory=list)
    retrain_signals: list[dict] = field(default_factory=list)
    last_updated: Optional[datetime] = None


@dataclass  
class SystemState:
    """
    Combined system state from all event types.
    """
    betting: BettingState = field(default_factory=BettingState)
    health: HealthState = field(default_factory=HealthState)
    model: ModelState = field(default_factory=ModelState)
    events_processed: int = 0
    last_event_timestamp: Optional[datetime] = None