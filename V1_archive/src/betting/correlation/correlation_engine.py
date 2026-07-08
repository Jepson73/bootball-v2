"""
Correlation Engine - Risk adjustment based on market correlations.

Adjusts portfolio selection based on correlation between markets:
- BTTS ↔ OU2.5 highly correlated
- OU1.5 ↔ BTTS moderately correlated
- H2H partially independent
- Same fixture = shared latent risk
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.events.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


# Default correlation priors (Pearson-like)
DEFAULT_CORRELATION = {
    ("btts", "ou25"): 0.65,
    ("btts", "ou15"): 0.45,
    ("ou25", "ou15"): 0.70,
    ("h2h", "btts"): 0.20,
    ("h2h", "ou25"): 0.15,
    ("h2h", "ou15"): 0.10,
}


def _make_symmetric(corr: dict) -> dict:
    """Ensure correlation matrix is symmetric."""
    result = {}
    for (a, b), v in corr.items():
        result[(a, b)] = v
        result[(b, a)] = v
    return result


DEFAULT_CORRELATION = _make_symmetric(DEFAULT_CORRELATION)


@dataclass
class CorrelationConfig:
    """Configuration for correlation risk."""
    alpha: float = 0.3  # Risk sensitivity (was 0.5)
    max_exposure_per_fixture: float = 0.05  # 5% bankroll per fixture
    max_correlation_penalty: float = 0.8  # Max stake reduction
    min_independence_weight: float = 0.2  # Minimum weight for independent


@dataclass
class ScoredBet:
    """Bet with correlation-adjusted score."""
    fixture_id: int
    market: str
    outcome: str
    odds: float
    ev: float
    kelly_fraction: float
    base_stake: float
    correlation_risk: float = 0.0
    adjusted_ev: float = 0.0
    final_stake: float = 0.0
    rejected: bool = False
    reject_reason: str = ""


class CorrelationEngine:
    """
    Applies correlation risk adjustments to portfolio.
    
    Flow:
    1. Accept candidate bets
    2. Compute correlation risk vs portfolio
    3. Adjust EV downward
    4. Dampen stakes
    5. Apply fixture cap
    6. Return adjusted bets
    """
    
    def __init__(self, config: CorrelationConfig = None):
        self.config = config or CorrelationConfig()
        self.correlation_matrix = DEFAULT_CORRELATION.copy()
        self._history: list[dict] = []  # For persistence
        
        logger.info("CorrelationEngine initialized")
    
    def adjust_portfolio(
        self,
        candidates: list[dict],
        bankroll: float
    ) -> list[ScoredBet]:
        """
        Adjust portfolio based on correlation risk.
        
        Args:
            candidates: List of candidate bets
            bankroll: Current bankroll
            
        Returns:
            List of ScoredBet with adjusted stakes
        """
        if not candidates:
            return []
        
        # Convert to ScoredBet
        scored = []
        for c in candidates:
            scored.append(ScoredBet(
                fixture_id=c.get("fixture_id", 0),
                market=c.get("market", "h2h"),
                outcome=c.get("outcome", ""),
                odds=c.get("odds", 0),
                ev=c.get("ev", 0),
                kelly_fraction=c.get("kelly_fraction", c.get("kelly", 0)),
                base_stake=bankroll * c.get("kelly_fraction", c.get("kelly", 0)),
            ))
        
        # Sort by base EV descending
        scored.sort(key=lambda x: x.ev, reverse=True)
        
        # Apply correlation adjustments
        selected: list[ScoredBet] = []
        fixture_exposure = {}
        
        for bet in scored:
            # Skip if fixture already at max
            fixture_key = bet.fixture_id
            current_fixture = fixture_exposure.get(fixture_key, 0)
            max_fixture = bankroll * self.config.max_exposure_per_fixture
            
            if current_fixture >= max_fixture:
                bet.rejected = True
                bet.reject_reason = "fixture_cap"
                continue
            
            # Calculate correlation risk vs selected portfolio
            corr_risk = self._calculate_correlation_risk(bet, selected)
            bet.correlation_risk = corr_risk
            
            # Adjust EV
            adjusted_ev = bet.ev - (self.config.alpha * corr_risk)
            bet.adjusted_ev = max(0, adjusted_ev)
            
            # Skip if adjusted EV is too low
            if adjusted_ev < 0.01:
                bet.rejected = True
                bet.reject_reason = "low_adjusted_ev"
                continue
            
            # Dampen stake based on correlation
            dampen_factor = 1.0 - min(corr_risk, self.config.max_correlation_penalty)
            final_stake = bet.base_stake * dampen_factor
            
            # Apply fixture cap
            remaining = max_fixture - current_fixture
            if final_stake > remaining:
                final_stake = remaining
            
            # Minimum stake
            if final_stake < 1:
                bet.rejected = True
                bet.reject_reason = "stake_too_small"
                continue
            
            bet.final_stake = final_stake
            
            # Add to selected
            selected.append(bet)
            fixture_exposure[fixture_key] = current_fixture + final_stake
        
        # Log results
        self._log_correlation_analysis(selected, scored)
        
        return selected
    
    def _calculate_correlation_risk(
        self,
        bet: ScoredBet,
        portfolio: list[ScoredBet]
    ) -> float:
        """Calculate correlation risk of a bet vs portfolio."""
        if not portfolio:
            return 0.0
        
        risk = 0.0
        
        for existing in portfolio:
            # Same fixture = moderate correlation (handled by fixture cap)
            if existing.fixture_id == bet.fixture_id:
                risk += 0.25
                continue
            
            # Market correlation
            key = (bet.market, existing.market)
            corr = self.correlation_matrix.get(key, 0.0)
            
            # Weight by proportion of portfolio (not absolute stake)
            total_stake = sum(p.final_stake for p in portfolio)
            if total_stake > 0:
                weight = existing.final_stake / total_stake
                risk += corr * weight
        
        return min(risk, 1.0)
    
    def _log_correlation_analysis(
        self,
        selected: list[ScoredBet],
        all_bets: list[ScoredBet]
    ) -> None:
        """Log correlation analysis results."""
        rejected = [b for b in all_bets if b.rejected]
        reasons = {}
        for b in rejected:
            reasons[b.reject_reason] = reasons.get(b.reject_reason, 0) + 1
        
        logger.info(f"[CORRELATION] Selected: {len(selected)}, Rejected: {len(rejected)}")
        
        if reasons:
            logger.info(f"[CORRELATION] Rejection reasons: {reasons}")
        
        # Log portfolio risk
        if selected:
            avg_risk = sum(b.correlation_risk for b in selected) / len(selected)
            logger.info(f"[CORRELATION] Portfolio avg risk: {avg_risk:.2f}")
            
            # Emit event
            market_risks = {}
            for b in selected:
                market_risks[b.market] = b.correlation_risk
            
            event_bus.emit(Events.CORRELATION_ANALYZED, {
                "fixture_count": len(set(b.fixture_id for b in selected)),
                "market_risks": market_risks,
                "portfolio_risk": avg_risk,
                "bets_selected": len(selected),
            })
    
    def get_correlation(self, market_a: str, market_b: str) -> float:
        """Get correlation between two markets."""
        key = (market_a, market_b)
        return self.correlation_matrix.get(key, 0.0)


# Global instance
_engine: Optional[CorrelationEngine] = None


def get_correlation_engine() -> CorrelationEngine:
    """Get global correlation engine."""
    global _engine
    if _engine is None:
        _engine = CorrelationEngine()
    return _engine
