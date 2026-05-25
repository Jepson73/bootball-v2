"""
Portfolio Optimizer - Global portfolio-level capital allocation.

Optimizes across all markets (H2H, BTTS, OU15, OU25) based on:
- Expected value
- Model confidence
- Correlation avoidance
- Diversification constraints
- Risk caps
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.betting.correlation import get_correlation_engine

logger = logging.getLogger(__name__)


# =========================================================
# Configuration
# =========================================================

@dataclass
class PortfolioConfig:
    """Configuration for portfolio optimization."""
    target_market_exposure: dict[str, float] = field(default_factory=lambda: {
        "h2h": 0.25,
        "btts": 0.25,
        "ou25": 0.25,
        "ou15": 0.25,
    })
    max_total_exposure: float = 0.15
    max_per_bet: float = 0.03
    max_per_fixture: float = 0.05
    min_ev_threshold: float = 0.02
    min_confidence: float = 0.4
    max_bets_per_fixture: int = 2


# =========================================================
# Data Structures
# =========================================================

@dataclass
class CandidateBet:
    """Unified candidate bet format."""
    fixture_id: int
    market: str
    outcome: str
    odds: float
    prob: float
    ev: float
    kelly_fraction: float
    model_confidence: float
    correlation_key: str = ""
    normalized_score: float = 0.0
    adjusted_ev: float = 0.0
    correlation_risk: float = 0.0
    correlation_rejected: bool = False
    reject_reason: str = ""
    
    def __post_init__(self):
        if not self.correlation_key:
            self.correlation_key = f"fixture_{self.fixture_id}"


@dataclass
class OptimizedBet:
    """Output bet with portfolio allocation."""
    fixture_id: int
    market: str
    outcome: str
    odds: float
    prob: float
    ev: float
    kelly_fraction: float
    model_confidence: float
    final_stake: float
    portfolio_weight: float
    correlation_key: str
    adjusted_ev: float = 0.0
    correlation_risk: float = 0.0


@dataclass
class PortfolioResult:
    """Result of portfolio optimization."""
    bets: list[OptimizedBet]
    total_stake: float
    bankroll: float
    exposure: float
    market_distribution: dict[str, float]
    rejected_count: int
    input_count: int


# =========================================================
# Portfolio Optimizer
# =========================================================

class PortfolioOptimizer:
    """
    Optimizes capital allocation across all markets.
    
    Steps:
    1. Filter low quality bets
    2. Calculate normalized opportunity scores
    3. Handle correlation (avoid same fixture stacking)
    4. Apply market diversification targets
    5. Allocate stakes with risk caps
    6. Return optimized portfolio
    """
    
    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()
        logger.info("PortfolioOptimizer initialized")
    
    def optimize(
        self, 
        candidates: list[dict], 
        bankroll: float
    ) -> PortfolioResult:
        """
        Optimize portfolio from candidate bets.
        
        Args:
            candidates: List of value bets (dict format)
            bankroll: Current bankroll
            
        Returns:
            PortfolioResult with optimized bets
        """
        if not candidates:
            return self._empty_result(bankroll)
        
        input_count = len(candidates)
        
        # Step 1: Filter low quality
        filtered = self._filter_candidates(candidates)
        logger.info(f"Filtered {input_count - len(filtered)} low-quality bets")
        
        if not filtered:
            return self._empty_result(bankroll)
        
        # Convert to CandidateBet objects
        bets = [self._dict_to_candidate(c) for c in filtered]
        
        # Step 2: Normalize opportunity scores
        bets = self._normalize_scores(bets)
        
        # Step 3: Correlation handling (basic - per fixture limit)
        bets = self._handle_correlation(bets)
        
        # Step 3b: Advanced correlation risk adjustment
        bets = self._apply_correlation_risk(bets, bankroll)
        
        # Step 4: Market diversification
        bets = self._apply_market_diversification(bets)
        
        # Step 5: Stake allocation
        bets = self._allocate_stakes(bets, bankroll)
        
        # Step 6: Risk caps
        allocated, rejected = self._apply_risk_caps(bets, bankroll)
        
        # Build result
        result = self._build_result(
            allocated=allocated,
            rejected=rejected,
            bankroll=bankroll,
            input_count=input_count
        )
        
        # Log
        self._log_portfolio(result)
        
        return result
    
    def _empty_result(self, bankroll: float) -> PortfolioResult:
        """Return empty result."""
        return PortfolioResult(
            bets=[],
            total_stake=0.0,
            bankroll=bankroll,
            exposure=0.0,
            market_distribution={},
            rejected_count=0,
            input_count=0
        )
    
    def _dict_to_candidate(self, d: dict) -> CandidateBet:
        """Convert dict to CandidateBet."""
        return CandidateBet(
            fixture_id=d.get("fixture_id", 0),
            market=d.get("market", "h2h"),
            outcome=d.get("outcome", ""),
            odds=d.get("odds", 0),
            prob=d.get("prob", d.get("our_prob", 0.5)),
            ev=d.get("ev", 0),
            kelly_fraction=d.get("kelly_fraction", d.get("kelly", 0)),
            model_confidence=d.get("model_confidence", 0.5),
            correlation_key=d.get("correlation_key", f"fixture_{d.get('fixture_id', 0)}")
        )
    
    def _filter_candidates(self, candidates: list[dict]) -> list[dict]:
        """Step 1: Filter low quality bets."""
        filtered = []
        for c in candidates:
            ev = c.get("ev", 0)
            kelly = c.get("kelly_fraction", c.get("kelly", 0))
            confidence = c.get("model_confidence", 0.5)
            
            if ev < self.config.min_ev_threshold:
                continue
            if kelly <= 0:
                continue
            if confidence < self.config.min_confidence:
                continue
            if c.get("odds", 0) < 1.6:
                continue
            
            filtered.append(c)
        
        return filtered
    
    def _normalize_scores(self, bets: list[CandidateBet]) -> list[CandidateBet]:
        """Step 2: Calculate normalized opportunity scores."""
        if not bets:
            return bets
        
        # Score = EV * model_confidence
        for bet in bets:
            bet.opportunity_score = bet.ev * bet.model_confidence
        
        # Normalize scores
        total_score = sum(b.opportunity_score for b in bets)
        if total_score > 0:
            for bet in bets:
                bet.normalized_score = bet.opportunity_score / total_score
        else:
            # Equal weight if no score
            equal_weight = 1.0 / len(bets)
            for bet in bets:
                bet.normalized_score = equal_weight
        
        return bets
    
    def _handle_correlation(self, bets: list[CandidateBet]) -> list[CandidateBet]:
        """Step 3: Avoid stacking bets on same fixture."""
        # Group by correlation_key
        from collections import defaultdict
        groups = defaultdict(list)
        for bet in bets:
            groups[bet.correlation_key].append(bet)
        
        result = []
        for key, group in groups.items():
            # Sort by score (descending)
            sorted_group = sorted(group, key=lambda x: x.normalized_score, reverse=True)
            
            # Keep top N per fixture
            max_keep = self.config.max_bets_per_fixture
            kept = sorted_group[:max_keep]
            
            # Mark as correlation-rejected if beyond limit
            for i, bet in enumerate(sorted_group):
                if i >= max_keep:
                    bet.correlation_rejected = True
                else:
                    bet.correlation_rejected = False
            
            result.extend(kept)
        
        return result
    
    def _apply_correlation_risk(
        self, 
        bets: list[CandidateBet], 
        bankroll: float
    ) -> list[CandidateBet]:
        """Apply advanced correlation risk using correlation engine."""
        if not bets:
            return bets
        
        # Convert to dict format for correlation engine
        candidates = []
        for b in bets:
            candidates.append({
                "fixture_id": b.fixture_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "ev": b.ev,
                "kelly_fraction": b.kelly_fraction,
            })
        
        # Use correlation engine
        corr_engine = get_correlation_engine()
        scored_bets = corr_engine.adjust_portfolio(candidates, bankroll)
        
        # Map results back to CandidateBet
        scored_map = {
            (s.fixture_id, s.market, s.outcome): s 
            for s in scored_bets
        }
        
        # Update bets with correlation-adjusted values
        for bet in bets:
            key = (bet.fixture_id, bet.market, bet.outcome)
            if key in scored_map:
                scored = scored_map[key]
                bet.adjusted_ev = scored.adjusted_ev
                bet.correlation_risk = scored.correlation_risk
                if scored.rejected:
                    bet.correlation_rejected = True
                    bet.reject_reason = scored.reject_reason
            else:
                # Not in selected portfolio
                bet.correlation_rejected = True
                bet.reject_reason = "correlation_filter"
        
        # Return only non-rejected
        accepted = [b for b in bets if not b.correlation_rejected]
        
        logger.info(f"[CORRELATION] Passed: {len(accepted)}/{len(bets)}")
        
        return accepted
    
    def _apply_market_diversification(self, bets: list[CandidateBet]) -> list[CandidateBet]:
        """Step 4: Apply market diversification targets."""
        # Count current market distribution
        market_counts = {}
        market_scores = {}
        for bet in bets:
            market = bet.market
            market_counts[market] = market_counts.get(market, 0) + 1
            market_scores[market] = market_scores.get(market, 0) + bet.normalized_score
        
        total = len(bets)
        if total == 0:
            return bets
        
        # Calculate weights and apply diversification boost/penalty
        result = []
        for bet in bets:
            current_pct = market_counts.get(bet.market, 0) / total
            target_pct = self.config.target_market_exposure.get(bet.market, 0.25)
            
            # If under target, boost; if over, decay
            if current_pct < target_pct:
                bet.diversification_weight = 1.0 + (target_pct - current_pct)
            else:
                bet.diversification_weight = 1.0 - (current_pct - target_pct) * 0.5
            
            # Clamp weight
            bet.diversification_weight = max(0.3, min(1.5, bet.diversification_weight))
            
            result.append(bet)
        
        return result
    
    def _allocate_stakes(self, bets: list[CandidateBet], bankroll: float) -> list[CandidateBet]:
        """Step 5: Allocate stakes based on scores and weights."""
        for bet in bets:
            # Base stake from Kelly
            base_stake = bankroll * bet.kelly_fraction
            
            # Adjust by normalized score and diversification weight
            adjusted_stake = base_stake * bet.normalized_score * bet.diversification_weight
            
            bet.allocated_stake = adjusted_stake
        
        return bets
    
    def _apply_risk_caps(
        self, 
        bets: list[CandidateBet], 
        bankroll: float
    ) -> tuple:
        """Step 6: Apply risk caps."""
        max_total = bankroll * self.config.max_total_exposure
        max_per_bet = bankroll * self.config.max_per_bet
        max_per_fixture = bankroll * self.config.max_per_fixture
        
        allocated = []
        rejected = []
        total_stake = 0
        
        # Sort by score (descending)
        sorted_bets = sorted(bets, key=lambda x: x.normalized_score, reverse=True)
        
        # Track fixture exposure
        fixture_exposure = {}
        
        for bet in sorted_bets:
            stake = bet.allocated_stake
            
            # Check per-bet cap
            if stake > max_per_bet:
                stake = max_per_bet
            
            # Check fixture cap
            fixture_key = bet.correlation_key
            current_fixture = fixture_exposure.get(fixture_key, 0)
            remaining_fixture = max_per_fixture - current_fixture
            
            if remaining_fixture <= 0:
                rejected.append(bet)
                continue
            
            if stake > remaining_fixture:
                stake = remaining_fixture
            
            # Check total cap
            if total_stake + stake > max_total:
                rejected.append(bet)
                continue
            
            # Minimum stake
            if stake < 1:
                rejected.append(bet)
                continue
            
            # Allocate
            bet.final_stake = stake
            bet.portfolio_weight = bet.normalized_score * bet.diversification_weight
            allocated.append(bet)
            
            total_stake += stake
            fixture_exposure[fixture_key] = current_fixture + stake
        
        return allocated, rejected
    
    def _build_result(
        self,
        allocated: list[CandidateBet],
        rejected: list[CandidateBet],
        bankroll: float,
        input_count: int
    ) -> PortfolioResult:
        """Build final portfolio result."""
        # Convert to OptimizedBet
        optimized_bets = []
        for bet in allocated:
            optimized_bets.append(OptimizedBet(
                fixture_id=bet.fixture_id,
                market=bet.market,
                outcome=bet.outcome,
                odds=bet.odds,
                prob=bet.prob,
                ev=bet.ev,
                kelly_fraction=bet.kelly_fraction,
                model_confidence=bet.model_confidence,
                final_stake=bet.final_stake,
                portfolio_weight=bet.portfolio_weight,
                correlation_key=bet.correlation_key,
                adjusted_ev=bet.adjusted_ev,
                correlation_risk=bet.correlation_risk
            ))
        
        # Market distribution
        market_dist = {}
        total = len(optimized_bets)
        for ob in optimized_bets:
            market_dist[ob.market] = market_dist.get(ob.market, 0) + 1
        
        if total > 0:
            market_dist = {k: v/total * 100 for k, v in market_dist.items()}
        
        total_stake = sum(ob.final_stake for ob in optimized_bets)
        
        return PortfolioResult(
            bets=optimized_bets,
            total_stake=total_stake,
            bankroll=bankroll,
            exposure=total_stake / bankroll if bankroll > 0 else 0,
            market_distribution=market_dist,
            rejected_count=len(rejected),
            input_count=input_count
        )
    
    def _log_portfolio(self, result: PortfolioResult) -> None:
        """Log portfolio optimization results."""
        logger.info(f"Portfolio built: {len(result.bets)} bets from {result.input_count} candidates")
        logger.info(f"Total stake: SEK {result.total_stake:.2f}, exposure: {result.exposure * 100:.1f}%")
        logger.info(f"Market distribution: {result.market_distribution}")


# Global instance
_optimizer: Optional[PortfolioOptimizer] = None


def get_portfolio_optimizer() -> PortfolioOptimizer:
    """Get global portfolio optimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = PortfolioOptimizer()
    return _optimizer


def optimize_portfolio(candidates: list[dict], bankroll: float) -> list[dict]:
    """
    Convenience function to optimize portfolio.
    
    Args:
        candidates: List of value bet dicts
        bankroll: Current bankroll
        
    Returns:
        List of optimized bet dicts with final_stake
    """
    optimizer = get_portfolio_optimizer()
    result = optimizer.optimize(candidates, bankroll)
    
    return [
        {
            "fixture_id": bet.fixture_id,
            "market": bet.market,
            "outcome": bet.outcome,
            "odds": bet.odds,
            "ev": bet.ev,
            "our_prob": bet.prob,
            "kelly_fraction": bet.kelly_fraction,
            "model_confidence": bet.model_confidence,
            "final_stake": bet.final_stake,
            "portfolio_weight": bet.portfolio_weight,
        }
        for bet in result.bets
    ]
