"""
Stress testing models for portfolio analysis.
"""

import numpy as np
import logging
from typing import List, Dict, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StressResult:
    """Result of stress test."""
    scenario: str
    initial_value: float
    stressed_value: float
    change_pct: float
    severity: float


class StressModels:
    """Stress testing models for portfolio analysis."""
    
    def __init__(self):
        self.results: List[StressResult] = []
    
    def test_correlation_shock(self, portfolio: list, correlations: dict) -> StressResult:
        """Test portfolio under correlation shock."""
        # Calculate current portfolio correlation exposure
        initial_exposure = self._calc_correlation_exposure(portfolio, correlations)
        
        # Shock correlations
        stressed_correlations = {k: min(0.95, v + 0.3) for k, v in correlations.items()}
        stressed_exposure = self._calc_correlation_exposure(portfolio, stressed_correlations)
        
        return StressResult(
            scenario="correlation_crash",
            initial_value=initial_exposure,
            stressed_value=stressed_exposure,
            change_pct=(stressed_exposure - initial_exposure) / max(initial_exposure, 0.01),
            severity=0.8
        )
    
    def test_odds_drift(self, portfolio: list) -> StressResult:
        """Test portfolio under odds drift."""
        initial_ev = sum(b.get("expected_return", 0) for b in portfolio)
        
        # Apply odds drift
        stressed_ev = 0
        for bet in portfolio:
            odds = bet.get("odds", 2.0)
            drift = np.random.uniform(0.85, 0.95)
            stressed_odds = odds * drift
            stressed_ev += (stressed_odds - 1) if bet.get("won", True) else -1
        
        return StressResult(
            scenario="odds_repricing_shock",
            initial_value=initial_ev,
            stressed_value=stressed_ev,
            change_pct=(stressed_ev - initial_ev) / max(abs(initial_ev), 0.01),
            severity=0.7
        )
    
    def test_model_miscalibration(self, portfolio: list) -> StressResult:
        """Test portfolio under model miscalibration."""
        initial_wins = sum(1 for b in portfolio if b.get("expected_outcome", 0) > 0)
        
        # Apply +/- 15% probability shift
        stressed_wins = 0
        for bet in portfolio:
            prob = bet.get("prob", 0.5)
            shift = np.random.uniform(-0.15, 0.15)
            adjusted_prob = max(0.01, min(0.99, prob + shift))
            # Simulate outcome
            if np.random.random() < adjusted_prob:
                stressed_wins += 1
        
        change_pct = (stressed_wins - initial_wins) / max(initial_wins, 1)
        
        return StressResult(
            scenario="model_bias_shift",
            initial_value=initial_wins,
            stressed_value=stressed_wins,
            change_pct=change_pct,
            severity=0.6
        )
    
    def test_regime_flip(self, portfolio: list, current_regime: str) -> StressResult:
        """Test portfolio under regime flip."""
        if current_regime == "defensive":
            return StressResult(
                scenario="regime_flip",
                initial_value=1.0,
                stressed_value=1.0,
                change_pct=0,
                severity=0
            )
        
        # Simulate worst case: regime flips to defensive
        stress_multiplier = 1.5
        
        return StressResult(
            scenario="regime_flip",
            initial_value=1.0,
            stressed_value=stress_multiplier,
            change_pct=stress_multiplier - 1.0,
            severity=0.9
        )
    
    def test_concentration_risk(self, portfolio: list) -> StressResult:
        """Test portfolio for concentration risk."""
        if not portfolio:
            return StressResult(
                scenario="concentration_risk",
                initial_value=0,
                stressed_value=0,
                change_pct=0,
                severity=0
            )
        
        # Calculate concentration (Herfindahl index)
        stakes = [b.get("stake", 0) for b in portfolio]
        total = sum(stakes)
        if total == 0:
            return StressResult(
                scenario="concentration_risk",
                initial_value=0,
                stressed_value=0,
                change_pct=0,
                severity=0
            )
        
        weights = [s / total for s in stakes]
        concentration = sum(w ** 2 for w in weights)
        
        # High concentration is bad
        severity = concentration / 0.5 if concentration > 0.5 else concentration
        
        return StressResult(
            scenario="concentration_risk",
            initial_value=concentration,
            stressed_value=concentration,
            change_pct=0,
            severity=severity
        )
    
    def _calc_correlation_exposure(self, portfolio: list, correlations: dict) -> float:
        """Calculate portfolio correlation exposure."""
        markets = [b.get("market", "") for b in portfolio]
        if len(markets) < 2:
            return 0.0
        
        total_corr = 0.0
        count = 0
        for i, m1 in enumerate(markets):
            for m2 in markets[i+1:]:
                key = (m1, m2)
                corr = correlations.get(key, correlations.get((m2, m1), 0.1))
                total_corr += corr
                count += 1
        
        return total_corr / max(count, 1)
    
    def run_all_stress_tests(
        self,
        portfolio: list,
        correlations: dict,
        regime: str
    ) -> Dict[str, StressResult]:
        """Run all stress tests."""
        results = {}
        
        results["correlation_shock"] = self.test_correlation_shock(portfolio, correlations)
        results["odds_drift"] = self.test_odds_drift(portfolio)
        results["model_miscalibration"] = self.test_model_miscalibration(portfolio)
        results["regime_flip"] = self.test_regime_flip(portfolio, regime)
        results["concentration_risk"] = self.test_concentration_risk(portfolio)
        
        return results
