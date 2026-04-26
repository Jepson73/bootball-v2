"""
Stress scenario generator for adversarial testing.
"""

import random
from dataclasses import dataclass
from typing import List


@dataclass
class StressScenario:
    """Represents a stress scenario."""
    name: str
    description: str
    severity: float  # 0-1 scale
    apply: callable


class StressScenarios:
    """Generates stress scenarios for portfolio testing."""
    
    SCENARIO_DEFINITIONS = [
        ("correlation_crash", "All correlations spike toward 1.0", 0.8),
        ("odds_repricing_shock", "Bookmaker odds shift against positions", 0.7),
        ("model_bias_shift", "Model probabilities shift by 10-20%", 0.6),
        ("liquidity_drain", "Unable to get full stake at desired odds", 0.5),
        ("regime_flip", "Market regime shifts from bull to defensive", 0.9),
        ("clustered_losses", "Multiple similar bets lose in sequence", 0.7),
    ]
    
    def generate_daily_scenarios(self, count: int = 3) -> List[StressScenario]:
        """Generate a random set of scenarios for testing."""
        scenarios = []
        for name, desc, severity in self.SCENARIO_DEFINITIONS[:count]:
            scenarios.append(StressScenario(
                name=name,
                description=desc,
                severity=severity,
                apply=self._get_scenario_func(name)
            ))
        return scenarios
    
    def get_all_scenarios(self) -> List[StressScenario]:
        """Get all available scenarios."""
        return [
            StressScenario(name=name, description=desc, severity=sev, apply=self._get_scenario_func(name))
            for name, desc, sev in self.SCENARIO_DEFINITIONS
        ]
    
    def _get_scenario_func(self, name: str):
        """Get scenario apply function."""
        scenarios = {
            "correlation_crash": self._correlation_crash,
            "odds_repricing_shock": self._odds_repricing_shock,
            "model_bias_shift": self._model_bias_shift,
            "liquidity_drain": self._liquidity_drain,
            "regime_flip": self._regime_flip,
            "clustered_losses": self._clustered_losses,
        }
        return scenarios.get(name, lambda x: x)
    
    def _correlation_crash(self, portfolio: list, correlations: dict) -> dict:
        """Simulate correlations spike to 0.9+"""
        modified = correlations.copy()
        for key in modified:
            modified[key] = min(0.95, modified[key] + 0.3)
        return modified
    
    def _odds_repricing_shock(self, portfolio: list, **kwargs) -> list:
        """Simulate odds drift against positions."""
        import random
        modified = []
        for bet in portfolio:
            drift = random.uniform(0.95, 0.85)  # odds worsen by 5-15%
            modified.append({
                **bet,
                "odds": bet.get("odds", 2.0) * drift,
                "ev_adjusted": bet.get("expected_return", 0) * drift
            })
        return modified
    
    def _model_bias_shift(self, portfolio: list, **kwargs) -> list:
        """Simulate probability calibration error."""
        import random
        modified = []
        for bet in portfolio:
            bias = random.uniform(-0.15, 0.15)
            modified.append({
                **bet,
                "prob_adjusted": bet.get("prob", 0.5) + bias
            })
        return modified
    
    def _liquidity_drain(self, portfolio: list, **kwargs) -> list:
        """Simulate reduced stake availability."""
        modified = []
        for bet in portfolio:
            modified.append({
                **bet,
                "stake_available": bet.get("stake", 100) * 0.7
            })
        return modified
    
    def _regime_flip(self, portfolio: list, **kwargs) -> dict:
        """Simulate regime shift impact."""
        return {"regime": "defensive", "lambda_multiplier": 1.5}
    
    def _clustered_losses(self, portfolio: list, **kwargs) -> list:
        """Simulate losing streak on similar bets."""
        modified = []
        for bet in portfolio:
            modified.append({
                **bet,
                "loss_simulation": True,
                "expected_outcome": -1  # simulate loss
            })
        return modified
