"""
Scenarios - predefined strategy configurations for backtesting.

Enables easy comparison of different strategy settings.
"""

from typing import Optional
from src.backtesting.backtest_engine import BacktestEngine, DEFAULT_CONFIG


# Predefined scenario configurations
SCENARIOS = {
    "baseline": {
        "description": "Current production settings",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.25,
        "risk_scaling": "balanced",
        "market_filter": None,
    },
    "conservative": {
        "description": "Lower risk, higher EV threshold",
        "min_ev_threshold": 0.10,
        "kelly_multiplier": 0.15,
        "risk_scaling": "conservative",
        "market_filter": None,
    },
    "aggressive": {
        "description": "Higher risk, lower EV threshold",
        "min_ev_threshold": 0.02,
        "kelly_multiplier": 0.40,
        "risk_scaling": "aggressive",
        "market_filter": None,
    },
    "h2h_only": {
        "description": "Only head-to-head market",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.25,
        "risk_scaling": "balanced",
        "market_filter": ["h2h"],
    },
    "btts_only": {
        "description": "Only BTTS market",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.25,
        "risk_scaling": "balanced",
        "market_filter": ["btts"],
    },
    "no_bets": {
        "description": "Simulate with no bets placed",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.25,
        "risk_scaling": "balanced",
        "simulate_no_bets": True,
    },
    "high_kelly": {
        "description": "Aggressive Kelly sizing",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.50,
        "risk_scaling": "aggressive",
    },
    "low_kelly": {
        "description": "Conservative Kelly sizing",
        "min_ev_threshold": 0.05,
        "kelly_multiplier": 0.10,
        "risk_scaling": "conservative",
    },
}


def get_scenario(name: str) -> dict:
    """Get scenario configuration by name."""
    return SCENARIOS.get(name, SCENARIOS["baseline"])


def list_scenarios() -> dict:
    """List all available scenarios."""
    return {
        name: {"description": cfg["description"]}
        for name, cfg in SCENARIOS.items()
    }


class ScenarioBuilder:
    """
    Builder for custom scenarios.
    """
    
    def __init__(self):
        self._config = dict(DEFAULT_CONFIG)
        self._description = "Custom scenario"
    
    def with_ev_threshold(self, threshold: float) -> "ScenarioBuilder":
        """Set EV threshold."""
        self._config["min_ev_threshold"] = threshold
        return self
    
    def with_kelly_multiplier(self, mult: float) -> "ScenarioBuilder":
        """Set Kelly multiplier."""
        self._config["kelly_multiplier"] = mult
        return self
    
    def with_risk_scaling(self, scaling: str) -> "ScenarioBuilder":
        """Set risk scaling (conservative, balanced, aggressive)."""
        self._config["risk_scaling"] = scaling
        return self
    
    def with_market_filter(self, markets: list) -> "ScenarioBuilder":
        """Set market filter."""
        self._config["market_filter"] = markets
        return self
    
    def with_no_bets(self, no_bets: bool = True) -> "ScenarioBuilder":
        """Set simulate no bets flag."""
        self._config["simulate_no_bets"] = no_bets
        return self
    
    def with_initial_bankroll(self, amount: float) -> "ScenarioBuilder":
        """Set initial bankroll."""
        self._config["initial_bankroll"] = amount
        return self
    
    def with_stop_loss(self, pct: float) -> "ScenarioBuilder":
        """Set stop loss percentage."""
        self._config["stop_loss_pct"] = pct
        return self
    
    def with_description(self, desc: str) -> "ScenarioBuilder":
        """Set description."""
        self._description = desc
        return self
    
    def build(self) -> dict:
        """Build scenario config."""
        return dict(self._config)
    
    def run(self, days: int = 30) -> dict:
        """Run backtest with this scenario."""
        engine = BacktestEngine(self._config)
        return engine.run_backtest(
            since=None,  # Will use all events
            until=None
        )


def compare_scenarios(
    scenario_a: str,
    scenario_b: str,
    days: int = 30
) -> dict:
    """
    Compare two scenarios.
    
    Returns:
        Comparison of metrics between scenarios
    """
    config_a = get_scenario(scenario_a)
    config_b = get_scenario(scenario_b)
    
    engine_a = BacktestEngine(config_a)
    engine_b = BacktestEngine(config_b)
    
    results_a = engine_a.run_backtest()
    results_b = engine_b.run_backtest()
    
    return {
        "scenario_a": {
            "name": scenario_a,
            "description": config_a.get("description"),
            "metrics": results_a,
        },
        "scenario_b": {
            "name": scenario_b,
            "description": config_b.get("description"),
            "metrics": results_b,
        },
        "comparison": {
            "roi_delta": results_b["roi"] - results_a["roi"],
            "pnl_delta": results_b["total_pnl"] - results_a["total_pnl"],
            "bets_delta": results_b["settled_bets"] - results_a["settled_bets"],
            "drawdown_delta": results_b["max_drawdown"] - results_a["max_drawdown"],
        },
    }
