"""
PortfolioState - Persistent system memory for capital allocation.

This is the single source of financial truth for the entire system.
Every run produces an immutable state snapshot.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

PORTFOLIO_STATE_DIR = Path("/opt/projects/bootball/data/portfolio")


@dataclass
class PortfolioState:
    """
    Persistent system memory.
    
    This is the ONLY memory of the system.
    All learning and allocation decisions flow through state transitions.
    """
    timestamp: str
    
    # Allocations
    allocations: Dict[str, dict] = field(default_factory=dict)  # bet_id → allocation
    exposure_by_market: Dict[str, float] = field(default_factory=dict)
    exposure_by_league: Dict[str, float] = field(default_factory=dict)
    
    # Risk context
    risk_lambda: float = 1.0
    regime: str = "neutral"  # bull / neutral / defensive
    drawdown: float = 0.0
    volatility: float = 0.0
    
    # Performance tracking
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    roi: float = 0.0
    
    # Structural metrics
    correlation_matrix: Dict[str, float] = field(default_factory=dict)
    entropy: float = 0.0
    
    # Learning layer
    allocation_weights: Dict[str, float] = field(default_factory=lambda: {
        "h2h": 0.25, "btts": 0.25, "ou25": 0.25, "ou15": 0.25
    })
    model_confidence_scaling: Dict[str, float] = field(default_factory=dict)
    
    # Historical tracking
    run_count: int = 0
    historical_roi: List[float] = field(default_factory=list)
    historical_drawdown: List[float] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return asdict(self)
    
    def get(self, key: str, default=None):
        """PRODUCTION-GRADE: Dict-like access for compatibility."""
        return getattr(self, key, default)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PortfolioState':
        """Create from dict."""
        return cls(**data)
    
    def copy(self) -> 'PortfolioState':
        """Create a copy of the state."""
        return PortfolioState(
            timestamp=self.timestamp,
            allocations=dict(self.allocations),
            exposure_by_market=dict(self.exposure_by_market),
            exposure_by_league=dict(self.exposure_by_league),
            risk_lambda=self.risk_lambda,
            regime=self.regime,
            drawdown=self.drawdown,
            volatility=self.volatility,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            roi=self.roi,
            correlation_matrix=dict(self.correlation_matrix),
            entropy=self.entropy,
            allocation_weights=dict(self.allocation_weights),
            model_confidence_scaling=dict(self.model_confidence_scaling),
            run_count=self.run_count,
            historical_roi=list(self.historical_roi),
            historical_drawdown=list(self.historical_drawdown),
        )


@dataclass
class StateSnapshot:
    """Immutable snapshot of PortfolioState."""
    state: PortfolioState
    run_id: str
    event_type: str  # "allocation", "settlement", "learning"
    
    def save(self) -> None:
        """Save snapshot to JSONL."""
        PORTFOLIO_STATE_DIR.mkdir(parents=True, exist_ok=True)
        
        filename = f"state_{self.state.timestamp.replace(':', '-')}.jsonl"
        filepath = PORTFOLIO_STATE_DIR / filename
        
        with open(filepath, 'a') as f:
            f.write(json.dumps({
                "run_id": self.run_id,
                "event_type": self.event_type,
                "state": self.state.to_dict()
            }) + "\n")
        
        logger.info(f"[STATE] Saved snapshot: {filename}")
    
    @classmethod
    def load_latest(cls) -> Optional[PortfolioState]:
        """Load most recent state."""
        if not PORTFOLIO_STATE_DIR.exists():
            return None
        
        files = sorted(PORTFOLIO_STATE_DIR.glob("state_*.jsonl"), reverse=True)
        if not files:
            return None
        
        try:
            with open(files[0], 'r') as f:
                lines = f.readlines()
                if lines:
                    data = json.loads(lines[-1])
                    return PortfolioState.from_dict(data["state"])
        except Exception as e:
            logger.warning(f"[STATE] Failed to load latest: {e}")
        
        return None
    
    @classmethod
    def load_history(cls, limit: int = 10) -> List[PortfolioState]:
        """Load recent history."""
        if not PORTFOLIO_STATE_DIR.exists():
            return []
        
        files = sorted(PORTFOLIO_STATE_DIR.glob("state_*.jsonl"), reverse=True)[:limit]
        states = []
        
        for f in files:
            try:
                with open(f, 'r') as fp:
                    for line in fp:
                        data = json.loads(line)
                        states.append(PortfolioState.from_dict(data["state"]))
            except:
                continue
        
        return states
