"""
Capital Allocator - Portfolio-level risk control and allocation.

Allocates capital across bets to maximize risk-adjusted returns
while enforcing diversification constraints.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.events.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


# =========================================================
# Configuration
# =========================================================

@dataclass
class AllocationConfig:
    """Configuration for capital allocation."""
    max_exposure_per_market: dict[str, float] = field(default_factory=lambda: {
        "h2h": 0.35,
        "btts": 0.30,
        "ou25": 0.25,
        "ou15": 0.25,
    })
    max_exposure_per_league: float = 0.25
    max_exposure_per_hour: float = 0.40
    max_portfolio_exposure: float = 0.20
    min_stake_threshold: float = 5.0
    global_kelly_scale: float = 0.25
    decay_factor_overused: float = 0.7
    boost_factor_underused: float = 1.2
    min_bets_for_boost: int = 3


# =========================================================
# Data Structures
# =========================================================

@dataclass
class ValueBet:
    """Input value bet from producers."""
    fixture_id: int
    market: str
    outcome: str
    odds: float
    ev: float
    our_prob: float
    kelly_fraction: float
    league: str
    kickoff: datetime


@dataclass
class AllocatedBet:
    """Output allocated bet with stake and metadata."""
    fixture_id: int
    market: str
    outcome: str
    stake: float
    ev: float
    kelly_fraction: float
    allocation_weight: float
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class AllocationResult:
    """Result of allocation process."""
    allocated_bets: list[AllocatedBet]
    total_stake: float
    bankroll: float
    exposure: float
    market_distribution: dict[str, float]
    rejected_count: int
    total_input_bets: int


# =========================================================
# Core Allocator
# =========================================================

class CapitalAllocator:
    """
    Portfolio-level capital allocator.
    
    Enforces diversification across markets, leagues, and time windows.
    """
    
    def __init__(self, config: AllocationConfig = None):
        self.config = config or AllocationConfig()
        self._bankroll = 1000.0
        logger.info("CapitalAllocator initialized")
    
    def set_bankroll(self, bankroll: float) -> None:
        """Set current bankroll."""
        self._bankroll = bankroll
    
    def get_bankroll(self) -> float:
        """Get current bankroll."""
        return self._bankroll
    
    def allocate(self, bets: list[ValueBet], bankroll: float = None) -> AllocationResult:
        """
        Allocate capital across value bets.
        
        Args:
            bets: List of value bets to consider
            bankroll: Current bankroll (uses internal if not provided)
            
        Returns:
            AllocationResult with allocated bets
        """
        if bankroll is None:
            bankroll = self._bankroll
            
        if not bets:
            return self._empty_result(bankroll)
        
        total_input = len(bets)
        
        # Step 1: Raw Kelly calculation
        bets_with_raw = self._apply_raw_kelly(bets, bankroll)
        
        # Step 2: Market diversification
        market_caps = self._apply_market_caps(bets_with_raw, bankroll)
        
        # Step 3: League diversification
        league_caps = self._apply_league_caps(market_caps, bankroll)
        
        # Step 4: Time clustering risk
        time_caps = self._apply_time_clustering(league_caps, bankroll)
        
        # Step 5: EV weighting
        ev_weighted = self._apply_ev_weighting(time_caps)
        
        # Step 6: Hard constraints
        allocated, rejected = self._apply_hard_constraints(ev_weighted, bankroll)
        
        # Build result
        result = self._build_result(
            allocated=allocated,
            rejected=rejected,
            bankroll=bankroll,
            total_input=total_input,
            bets=bets
        )
        
        # Log allocation
        self._log_allocation(result)
        
        return result
    
    def _empty_result(self, bankroll: float) -> AllocationResult:
        """Return empty allocation result."""
        return AllocationResult(
            allocated_bets=[],
            total_stake=0.0,
            bankroll=bankroll,
            exposure=0.0,
            market_distribution={},
            rejected_count=0,
            total_input_bets=0
        )
    
    def _apply_raw_kelly(self, bets: list[ValueBet], bankroll: float) -> list[dict]:
        """Step 1: Calculate raw Kelly stakes."""
        result = []
        for bet in bets:
            raw_stake = bankroll * bet.kelly_fraction * self.config.global_kelly_scale
            result.append({
                "bet": bet,
                "raw_stake": raw_stake,
                "ev": bet.ev
            })
        return result
    
    def _apply_market_caps(self, bets: list[dict], bankroll: float) -> list[dict]:
        """Step 2: Apply market diversification caps."""
        # Count bets per market
        market_counts = {}
        market_ev_sum = {}
        for b in bets:
            market = b["bet"].market
            market_counts[market] = market_counts.get(market, 0) + 1
            market_ev_sum[market] = market_ev_sum.get(market, 0) + b["ev"]
        
        total_bets = len(bets)
        
        # Detect overused markets (>50% of bets)
        overused_markets = set()
        for market, count in market_counts.items():
            if count / total_bets > 0.5:
                overused_markets.add(market)
                logger.info(f"Market {market} is overused: {count}/{total_bets}")
        
        # Apply caps
        result = []
        for b in bets:
            market = b["bet"].market
            cap = self.config.max_exposure_per_market.get(market, 0.30)
            
            # Apply decay for overused markets
            if market in overused_markets:
                b["raw_stake"] *= self.config.decay_factor_overused
                b["risk_flags"] = b.get("risk_flags", [])
                b["risk_flags"].append(f"market_decay_{market}")
            
            result.append(b)
        
        return result
    
    def _apply_league_caps(self, bets: list[dict], bankroll: float) -> list[dict]:
        """Step 3: Apply league diversification caps."""
        # Group by league
        league_counts = {}
        for b in bets:
            league = b["bet"].league
            league_counts[league] = league_counts.get(league, 0) + 1
        
        # Apply caps per league
        result = []
        for b in bets:
            league = b["bet"].league
            league_count = league_counts.get(league, 0)
            total = len(bets)
            
            if league_count / total > self.config.max_exposure_per_league:
                b["raw_stake"] *= 0.5
                b["risk_flags"] = b.get("risk_flags", [])
                b["risk_flags"].append("league_concentration")
            
            result.append(b)
        
        return result
    
    def _apply_time_clustering(self, bets: list[dict], bankroll: float) -> list[dict]:
        """Step 4: Limit exposure per kickoff time window."""
        # Group bets by hour
        hour_buckets = {}
        for b in bets:
            kickoff = b["bet"].kickoff
            hour_key = kickoff.replace(minute=0, second=0, microsecond=0)
            if hour_key not in hour_buckets:
                hour_buckets[hour_key] = []
            hour_buckets[hour_key].append(b)
        
        # Apply caps per hour
        result = []
        for b in bets:
            kickoff = b["bet"].kickoff
            hour_key = kickoff.replace(minute=0, second=0, microsecond=0)
            hour_count = len(hour_buckets.get(hour_key, []))
            total = len(bets)
            
            if hour_count / total > self.config.max_exposure_per_hour:
                b["raw_stake"] *= 0.7
                b["risk_flags"] = b.get("risk_flags", [])
                b["risk_flags"].append("time_clustering")
            
            result.append(b)
        
        return result
    
    def _apply_ev_weighting(self, bets: list[dict]) -> list[dict]:
        """Step 5: Normalize weights by EV."""
        if not bets:
            return bets
        
        # Calculate total EV
        total_ev = sum(b["ev"] for b in bets)
        if total_ev == 0:
            return bets
        
        # Normalize weights
        result = []
        for b in bets:
            weight = b["ev"] / total_ev if total_ev > 0 else 0
            b["allocation_weight"] = weight
            b["final_stake"] = b["raw_stake"] * weight
            result.append(b)
        
        # Boost underrepresented high-EV markets
        market_counts = {}
        for b in bets:
            market = b["bet"].market
            market_counts[market] = market_counts.get(market, 0) + 1
        
        for b in bets:
            market = b["bet"].market
            count = market_counts.get(market, 0)
            if count < self.config.min_bets_for_boost and b["ev"] > 0.1:
                # Boost underrepresented markets with high EV
                b["final_stake"] *= self.config.boost_factor_underused
                b["risk_flags"] = b.get("risk_flags", [])
                b["risk_flags"].append(f"boost_{market}")
        
        return result
    
    def _apply_hard_constraints(self, bets: list[dict], bankroll: float) -> tuple:
        """Step 6: Apply hard constraints."""
        max_stake = bankroll * self.config.max_portfolio_exposure
        
        allocated = []
        rejected = []
        
        # Sort by EV descending
        sorted_bets = sorted(bets, key=lambda x: x.get("ev", 0), reverse=True)
        
        total_stake = 0
        for b in sorted_bets:
            stake = b.get("final_stake", 0)
            
            # Check minimum threshold
            if stake < self.config.min_stake_threshold:
                rejected.append(b)
                continue
            
            # Check max portfolio exposure
            if total_stake + stake > max_stake:
                rejected.append(b)
                continue
            
            # Add to allocated
            allocated.append(b)
            total_stake += stake
        
        return allocated, rejected
    
    def _build_result(
        self,
        allocated: list[dict],
        rejected: list[dict],
        bankroll: float,
        total_input: int,
        bets: list[ValueBet]
    ) -> AllocationResult:
        """Build final allocation result."""
        # Convert to AllocatedBet objects
        allocated_bets = []
        for b in allocated:
            bet = b["bet"]
            allocated_bets.append(AllocatedBet(
                fixture_id=bet.fixture_id,
                market=bet.market,
                outcome=bet.outcome,
                stake=b["final_stake"],
                ev=bet.ev,
                kelly_fraction=bet.kelly_fraction,
                allocation_weight=b.get("allocation_weight", 0),
                risk_flags=b.get("risk_flags", [])
            ))
        
        # Calculate market distribution
        market_dist = {}
        total = len(allocated_bets)
        for ab in allocated_bets:
            market_dist[ab.market] = market_dist.get(ab.market, 0) + 1
        
        if total > 0:
            market_dist = {k: v/total * 100 for k, v in market_dist.items()}
        
        total_stake = sum(ab.stake for ab in allocated_bets)
        
        return AllocationResult(
            allocated_bets=allocated_bets,
            total_stake=total_stake,
            bankroll=bankroll,
            exposure=total_stake / bankroll if bankroll > 0 else 0,
            market_distribution=market_dist,
            rejected_count=len(rejected),
            total_input_bets=total_input
        )
    
    def _log_allocation(self, result: AllocationResult) -> None:
        """Log allocation results."""
        logger.info(f"[ALLOCATOR] Total bets input: {result.total_input_bets}")
        logger.info(f"[ALLOCATOR] Selected bets: {len(result.allocated_bets)}")
        logger.info(f"[ALLOCATOR] Total stake: SEK {result.total_stake:.2f}")
        logger.info(f"[ALLOCATOR] Exposure: {result.exposure * 100:.1f}%")
        logger.info(f"[ALLOCATOR] Market split: {result.market_distribution}")
        
        if result.rejected_count > 0:
            logger.info(f"[ALLOCATOR] Rejected: {result.rejected_count}")


# Global allocator
_allocator: Optional[CapitalAllocator] = None


def get_capital_allocator() -> CapitalAllocator:
    """Get global capital allocator."""
    global _allocator
    if _allocator is None:
        _allocator = CapitalAllocator()
    return _allocator
