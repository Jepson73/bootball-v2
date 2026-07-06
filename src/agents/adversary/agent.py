"""
Adversarial Agent - stress tests portfolio before execution.

Role: Critically evaluate Execution Strategist output, simulate worst-case,
detect vulnerabilities, and provide veto/adjust signals.

This is a non-executing analytical agent only.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass

from src.events.event_bus import event_bus
from src.agents.shared.events import AgentEvents
from src.agents.adversary.scenarios import StressScenarios
from src.agents.adversary.stress_models import StressModels, StressResult

logger = logging.getLogger(__name__)


# Risk thresholds
RISK_THRESHOLD_ACCEPT = 0.3
RISK_THRESHOLD_ADJUST = 0.6
RISK_THRESHOLD_REJECT = 0.8


@dataclass
class Vulnerability:
    """Represents a vulnerable position."""
    bet_id: str
    reason: str
    risk_multiplier: float


@dataclass
class AdversaryResult:
    """Result of adversarial analysis."""
    portfolio_risk_score: float
    max_drawdown_simulated: float
    failure_probability: float
    vulnerable_positions: List[Vulnerability]
    recommendation: str  # "accept" | "adjust" | "reject"
    stress_results: Dict[str, StressResult]


class AdversaryAgent:
    """
    Adversarial agent that stress tests portfolio before execution.
    
    Flow:
    1. Receive portfolio from Execution Strategist
    2. Run stress tests (correlation, odds drift, model failure, etc.)
    3. Identify vulnerable positions
    4. Calculate risk score
    5. Emit PORTFOLIO_STRESSED event
    6. If high risk, emit PORTFOLIO_VETOED event
    """
    
    def __init__(self):
        self._event_bus = event_bus
        self._scenarios = StressScenarios()
        self._stress_models = StressModels()
        
        # Subscribe to events
        self._event_bus.subscribe(AgentEvents.PORTFOLIO_ALLOCATED, self.handle_portfolio)
        
        self._last_portfolio: List[dict] = []
        self._last_risk_profile: Optional[dict] = None
        
        logger.info("[ADVERSARY] Agent initialized")
    
    def handle_portfolio(self, payload: dict) -> None:
        """Handle portfolio allocated event."""
        self._last_portfolio = payload.get("bets", [])
        self.run()
    
    def run(
        self,
        portfolio: List[dict] = None,
        risk_profile: dict = None,
        correlations: dict = None
    ) -> AdversaryResult:
        """
        Run adversarial analysis on portfolio.
        
        Args:
            portfolio: List of bets
            risk_profile: Risk profile from Risk Manager
            correlations: Correlation matrix
            
        Returns:
            AdversaryResult with analysis
        """
        portfolio = portfolio or self._last_portfolio
        risk_profile = risk_profile or self._last_risk_profile
        correlations = correlations or {}
        
        if not portfolio:
            logger.warning("[ADVERSARY] No portfolio to analyze")
            return self._empty_result()
        
        logger.info(f"[ADVERSARY] Analyzing {len(portfolio)} positions")
        
        # Step 1: Run stress tests
        stress_results = self._stress_models.run_all_stress_tests(
            portfolio,
            correlations,
            risk_profile.get("regime", "neutral") if risk_profile else "neutral"
        )
        
        # Step 2: Calculate risk score
        risk_score = self._calculate_risk_score(stress_results)
        
        # Step 3: Identify vulnerabilities
        vulnerabilities = self._identify_vulnerabilities(portfolio, stress_results)
        
        # Step 4: Simulate worst-case drawdown
        max_dd = self._simulate_worst_case(portfolio, stress_results)
        
        # Step 5: Determine failure probability
        fail_prob = self._calculate_failure_probability(stress_results)
        
        # Step 6: Make recommendation
        recommendation = self._make_recommendation(risk_score, fail_prob, vulnerabilities)
        
        # Step 7: Build result
        result = AdversaryResult(
            portfolio_risk_score=risk_score,
            max_drawdown_simulated=max_dd,
            failure_probability=fail_prob,
            vulnerable_positions=vulnerabilities,
            recommendation=recommendation,
            stress_results=stress_results
        )
        
        # Step 8: Emit events
        self._emit_events(result, portfolio)
        
        logger.info(f"[ADVERSARY] Risk score: {risk_score:.2f}, recommendation: {recommendation}")
        
        return result
    
    def _calculate_risk_score(self, stress_results: Dict[str, StressResult]) -> float:
        """Calculate overall risk score from stress tests."""
        weights = {
            "correlation_shock": 0.25,
            "odds_drift": 0.20,
            "model_miscalibration": 0.20,
            "regime_flip": 0.25,
            "concentration_risk": 0.10,
        }
        
        score = 0.0
        for scenario, result in stress_results.items():
            weight = weights.get(scenario, 0.1)
            # Use absolute change + severity
            impact = abs(result.change_pct) * result.severity
            score += weight * impact
        
        return min(1.0, score)
    
    def _identify_vulnerabilities(
        self,
        portfolio: List[dict],
        stress_results: Dict[str, StressResult]
    ) -> List[Vulnerability]:
        """Identify vulnerable positions."""
        vulnerabilities = []
        
        # Check concentration risk
        conc_result = stress_results.get("concentration_risk")
        if conc_result and conc_result.initial_value > 0.3:
            for bet in portfolio:
                vulnerabilities.append(Vulnerability(
                    bet_id=bet.get("bet_id", ""),
                    reason="High concentration in portfolio",
                    risk_multiplier=conc_result.severity
                ))
        
        # Check correlation shock
        corr_result = stress_results.get("correlation_crash")
        if corr_result and corr_result.change_pct > 0.5:
            # Find bets with similar markets
            markets = {}
            for bet in portfolio:
                m = bet.get("market", "")
                markets[m] = markets.get(m, 0) + 1
            
            for bet in portfolio:
                m = bet.get("market", "")
                if markets.get(m, 0) > 2:
                    vulnerabilities.append(Vulnerability(
                        bet_id=bet.get("bet_id", ""),
                        reason=f"Market cluster ({m}) vulnerable to correlation shock",
                        risk_multiplier=corr_result.severity
                    ))
        
        return vulnerabilities
    
    def _simulate_worst_case(
        self,
        portfolio: List[dict],
        stress_results: Dict[str, StressResult]
    ) -> float:
        """Simulate worst-case drawdown."""
        # Base: assume 50% of positions lose
        total_stake = sum(b.get("stake", 0) for b in portfolio)
        if total_stake == 0:
            return 0.0
        
        # Apply stress multipliers
        regime_result = stress_results.get("regime_flip", None)
        regime_multiplier = regime_result.stressed_value if regime_result else 1.0
        
        corr_result = stress_results.get("correlation_crash", None)
        corr_multiplier = 1 + abs(corr_result.change_pct) if corr_result else 1.0
        
        # Worst case: 50% loss * regime * correlation
        worst_case_loss = 0.5 * regime_multiplier * corr_multiplier
        
        return min(worst_case_loss, 1.0)  # Cap at 100%
    
    def _calculate_failure_probability(self, stress_results: Dict[str, StressResult]) -> float:
        """Calculate probability of significant loss."""
        # Based on stress test outcomes
        probs = []
        
        for result in stress_results.values():
            if result.change_pct < -0.3:  # Significant negative impact
                probs.append(result.severity)
        
        if not probs:
            return 0.0
        
        return min(sum(probs) / len(probs), 1.0)
    
    def _make_recommendation(
        self,
        risk_score: float,
        failure_prob: float,
        vulnerabilities: List[Vulnerability]
    ) -> str:
        """Make execution recommendation."""
        # Combine factors
        combined_risk = (risk_score * 0.4 + 
                        failure_prob * 0.4 + 
                        min(len(vulnerabilities) * 0.1, 0.2))
        
        if combined_risk < RISK_THRESHOLD_ACCEPT:
            return "accept"
        elif combined_risk < RISK_THRESHOLD_ADJUST:
            return "adjust"
        elif combined_risk < RISK_THRESHOLD_REJECT:
            return "adjust"
        else:
            return "reject"
    
    def _emit_events(self, result: AdversaryResult, portfolio: list) -> None:
        """Emit adversarial events."""
        # Always emit stress result
        self._event_bus.emit(AgentEvents.PORTFOLIO_STRESSED, {
            "risk_score": result.portfolio_risk_score,
            "max_drawdown": result.max_drawdown_simulated,
            "failure_probability": result.failure_probability,
            "vulnerabilities": [
                {"bet_id": v.bet_id, "reason": v.reason, "multiplier": v.risk_multiplier}
                for v in result.vulnerable_positions
            ],
            "recommendation": result.recommendation,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Emit veto if reject
        if result.recommendation == "reject":
            self._event_bus.emit(AgentEvents.PORTFOLIO_VETOED, {
                "risk_score": result.portfolio_risk_score,
                "reason": "High risk score exceeded threshold",
                "vulnerabilities": len(result.vulnerable_positions),
                "timestamp": datetime.utcnow().isoformat(),
            })
    
    def _empty_result(self) -> AdversaryResult:
        """Return empty result."""
        return AdversaryResult(
            portfolio_risk_score=0,
            max_drawdown_simulated=0,
            failure_probability=0,
            vulnerable_positions=[],
            recommendation="accept",
            stress_results={}
        )
    
    def apply_adjustments(self, portfolio: list) -> list:
        """Apply risk-based adjustments to portfolio."""
        result = self.run(portfolio)
        
        if result.recommendation == "reject":
            return []  # No execution
        
        if result.recommendation == "adjust":
            # Reduce stakes for vulnerable positions
            adjusted = []
            vulnerable_ids = {v.bet_id for v in result.vulnerable_positions}
            
            for bet in portfolio:
                if bet.get("bet_id") in vulnerable_ids:
                    # Reduce stake by 50%
                    adjusted.append({
                        **bet,
                        "stake": bet.get("stake", 0) * 0.5
                    })
                else:
                    adjusted.append(bet)
            
            return adjusted
        
        return portfolio


# Global instance
_agent: Optional[AdversaryAgent] = None


def get_adversary_agent() -> AdversaryAgent:
    """Get global adversarial agent."""
    global _agent
    if _agent is None:
        _agent = AdversaryAgent()
    return _agent
