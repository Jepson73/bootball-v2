import logging
import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class CorrelationAnalyzer:
    """Analyze prediction error correlations across markets and leagues."""
    
    market_errors: Dict[str, List[int]] = field(default_factory=dict)
    league_errors: Dict[int, List[int]] = field(default_factory=dict)
    predictions_by_market: Dict[str, List[Tuple[bool, bool, float]]] = field(default_factory=dict)
    predictions_by_league: Dict[int, List[Tuple[bool, bool, float]]] = field(default_factory=dict)
    
    total_predictions: int = 0
    total_errors: int = 0
    error_indices: List[int] = field(default_factory=list)
    correlation_failures: int = 0
    mean_gap: float = 0
    std_gap: float = 0
    correlation_by_market: Dict[str, int] = field(default_factory=dict)
    correlation_by_league: Dict[int, int] = field(default_factory=dict)
    market_specific_correlations: Dict[Tuple[str, str], List[float]] = field(default_factory=dict)
    
    def __init__(self):
        markets = ["h2h", "btts", "ou25", "ou15"]
        self.market_errors = {m: [] for m in markets}
        self.predictions_by_market = {m: [] for m in markets}
        self.correlation_by_market = {m: 0 for m in markets}
        self.market_specific_correlations = {}
        self.total_predictions = 0
        self.total_errors = 0
        self.error_indices = []
        self.correlation_failures = 0
        self.mean_gap = 0
        self.std_gap = 0
        self.league_errors = {}
        self.predictions_by_league = {}
        self.correlation_by_league = {}
        
    def add_prediction_result(self, market: str, league_id: int, predicted_correct: bool, ev: float):
        """Record a prediction outcome for correlation analysis."""
        
        self.total_predictions += 1
        
        if not predicted_correct:
            self.total_errors += 1
            self.error_indices.append(self.total_predictions - 1)
            
            self.market_errors[market].append(self.total_predictions - 1)
            self.league_errors.setdefault(league_id, []).append(self.total_predictions - 1)
        
        self.predictions_by_market.setdefault(market, []).append((predicted_correct, ev))
        self.predictions_by_league.setdefault(league_id, []).append((predicted_correct, ev))
    
    def compute_correlation_failures(self):
        """Detect correlated failures across markets and leagues."""
        
        if len(self.error_indices) < 2:
            return
        
        errors = np.array(self.error_indices)
        gaps = np.diff(errors)
        self.mean_gap = float(np.mean(gaps)) if len(gaps) > 0 else 0
        self.std_gap = float(np.std(gaps)) if len(gaps) > 0 else 0
        
        if self.mean_gap < 5:
            self.correlation_failures = 1
        
        for market, indices in self.market_errors.items():
            if len(indices) >= 2:
                gaps = np.diff(indices)
                if np.mean(gaps) < 10:
                    self.correlation_by_market[market] = 1
        
        for league, indices in self.league_errors.items():
            if len(indices) >= 2:
                gaps = np.diff(indices)
                if np.mean(gaps) < 10:
                    self.correlation_by_league[league] = 1
    
    def compute_market_pair_correlations(self):
        """Compute correlation between market errors."""
        
        markets = list(self.predictions_by_market.keys())
        
        for i, m1 in enumerate(markets):
            for m2 in markets[i+1:]:
                preds1 = self.predictions_by_market.get(m1, [])
                preds2 = self.predictions_by_market.get(m2, [])
                
                min_len = min(len(preds1), len(preds2))
                if min_len < 5:
                    continue
                
                correct1 = [1 if p[0] else 0 for p in preds1[:min_len]]
                correct2 = [1 if p[0] else 0 for p in preds2[:min_len]]
                
                if sum(correct1) > 2 and sum(correct2) > 2:
                    corr = np.corrcoef(correct1, correct2)[0, 1]
                    if not np.isnan(corr):
                        self.market_specific_correlations[(m1, m2)] = [float(corr)]
    
    def get_correlation_score(self) -> dict:
        """Get overall correlation failure metrics."""
        
        return {
            "correlated_failures": self.correlation_failures,
            "mean_error_gap": self.mean_gap,
            "std_error_gap": self.std_gap,
            "market_correlations": self.correlation_by_market,
            "league_correlations": self.correlation_by_league,
            "market_pair_correlations": {
                f"{k[0]}_{k[1]}": float(np.mean(v)) if v else 0 
                for k, v in self.market_specific_correlations.items()
            },
            "total_predictions": self.total_predictions,
            "total_errors": self.total_errors,
            "error_rate": self.total_errors / max(1, self.total_predictions),
        }


class StressTestScenarios:
    """Simulate adverse market conditions for stress testing."""
    
    @staticmethod
    def sudden_scoring_shift(predictions: list, shift_factor: float = 0.3) -> list:
        """Simulate sudden league-wide scoring pattern change."""
        np.random.seed(42)
        shifted = []
        for p in predictions:
            new_ev = p[2] * (1 - shift_factor)
            changed = np.random.random() < shift_factor
            new_correct = not p[0] if changed else p[0]
            shifted.append((new_correct, p[1], new_ev))
        return shifted
    
    @staticmethod
    def correlated_miscalibration(predictions: dict, markets: list, error_rate: float = 0.3) -> dict:
        """Simulate multiple markets failing together."""
        np.random.seed(42)
        result = {}
        
        if markets:
            correlated_errors = np.random.random(50) < error_rate
            
            for market in markets:
                preds = predictions.get(market, [])
                new_preds = []
                for i, p in enumerate(preds):
                    if i < len(correlated_errors) and correlated_errors[i]:
                        new_preds.append((not p[0], p[1], p[2] * 0.5))
                    else:
                        new_preds.append(p)
                result[market] = new_preds
        
        return result
    
    @staticmethod
    def prolonged_drift(predictions: list, drift_period: int = 14, degradation: float = 0.15) -> list:
        """Simulate prolonged drift with gradual degradation."""
        degraded = []
        for i, p in enumerate(predictions):
            if i < drift_period:
                factor = 1 - (degradation * i / drift_period)
                new_ev = p[2] * factor
                degraded.append((p[0], p[1], new_ev))
            else:
                degraded.append(p)
        return degraded
    
    @staticmethod
    def odds_bias_shift(predictions: list, bias: float = 0.1) -> list:
        """Simulate bookmaker odds bias shift."""
        biased = []
        for p in predictions:
            new_ev = p[2] + bias
            biased.append((p[0], p[1], new_ev))
        return biased


class DrawdownAnalyzer:
    """Analyze drawdown and tail risk metrics."""
    
    def __init__(self, initial_bankroll: float = 1000.0):
        self.initial_bankroll = initial_bankroll
        self.bankroll_history = [initial_bankroll]
        self.equity_curve = [0]
        self.drawdowns = []
        self.peak = initial_bankroll
    
    def add_result(self, pnl: float):
        """Add a bet result to the equity curve."""
        new_bankroll = self.bankroll_history[-1] + pnl
        self.bankroll_history.append(new_bankroll)
        
        if new_bankroll > self.peak:
            self.peak = new_bankroll
        
        drawdown = (self.peak - new_bankroll) / self.peak
        self.drawdowns.append(drawdown)
    
    def get_max_drawdown(self) -> float:
        """Get maximum drawdown."""
        return max(self.drawdowns) if self.drawdowns else 0
    
    def get_tail_risk(self, percentile: int = 5) -> float:
        """Get tail risk (worst N% outcomes)."""
        if len(self.drawdowns) < 10:
            return 0
        sorted_dd = sorted(self.drawdowns)
        idx = max(0, len(sorted_dd) * percentile // 100 - 1)
        return sorted_dd[idx]
    
    def get_longest_streak(self) -> Tuple[int, int]:
        """Get longest winning and losing streaks."""
        if len(self.bankroll_history) < 2:
            return 0, 0
        
        wins = []
        losses = []
        current_win = 0
        current_loss = 0
        
        for i in range(1, len(self.bankroll_history)):
            pnl = self.bankroll_history[i] - self.bankroll_history[i-1]
            if pnl > 0:
                current_win += 1
                current_loss = 0
                wins.append(current_win)
            elif pnl < 0:
                current_loss += 1
                current_win = 0
                losses.append(current_loss)
        
        return max(wins) if wins else 0, max(losses) if losses else 0


class SystemResilienceScorer:
    """Compute system resilience scores."""
    
    def __init__(self):
        self.scores = {}
    
    def compute_resilience(
        self,
        correlation_metrics: dict,
        drawdown_metrics: dict,
        rejection_metrics: dict,
        market: str = "all"
    ) -> dict:
        """Compute overall resilience score."""
        
        corr_score = 1.0 - min(1.0, correlation_metrics.get("error_rate", 0.5))
        
        dd_score = 1.0 - drawdown_metrics.get("max_drawdown", 1.0)
        
        reject_score = rejection_metrics.get("accepted_avg_ev", 0)
        reject_score = max(0, min(1, reject_score / 0.2))
        
        overall = (corr_score * 0.4 + dd_score * 0.3 + reject_score * 0.3)
        
        self.scores[market] = overall
        
        return {
            "market": market,
            "correlation_score": corr_score,
            "drawdown_score": dd_score,
            "rejection_quality_score": reject_score,
            "overall_resilience": overall,
            "component_weights": {
                "correlation": 0.4,
                "drawdown": 0.3,
                "rejection_quality": 0.3
            }
        }
    
    def compute_extended_resilience(
        self,
        correlation_metrics: dict,
        drawdown_metrics: dict,
        rejection_metrics: dict,
        latent_shock_metrics: dict,
        market: str = "all"
    ) -> dict:
        """Compute extended resilience with latent shock robustness."""
        
        base_resilience = self.compute_resilience(
            correlation_metrics, drawdown_metrics, rejection_metrics, market
        )
        
        cross_market_stability = latent_shock_metrics.get("cross_market_stability", 1.0)
        latent_shock_robustness = latent_shock_metrics.get("latent_shock_robustness", 1.0)
        avg_shock_systemic = latent_shock_metrics.get("avg_shock_systemic_ratio", 0)
        
        latent_score = (cross_market_stability * 0.5 + latent_shock_robustness * 0.5)
        
        extended_overall = (
            base_resilience.get("overall_resilience", 0) * 0.6 +
            latent_score * 0.4
        )
        
        return {
            "market": market,
            "base_resilience": base_resilience.get("overall_resilience", 0),
            "latent_shock_score": latent_score,
            "cross_market_stability": cross_market_stability,
            "latent_shock_robustness": latent_shock_robustness,
            "shock_systemic_ratio": avg_shock_systemic,
            "extended_resilience": extended_overall,
            "extended_component_weights": {
                "base_resilience": 0.6,
                "latent_shock_score": 0.4
            }
        }


def analyze_stress_results(predictions: list, market: str = "h2h") -> dict:
    """Analyze stress test results."""
    
    analyzer = CorrelationAnalyzer()
    drawdown_analyzer = DrawdownAnalyzer()
    
    for p in predictions:
        predicted_correct = p[0]
        actual_correct = p[1]
        ev = p[2]
        
        analyzer.add_prediction_result(market, 88, predicted_correct, ev)
        
        pnl = (ev + 1) * 10 - 10 if actual_correct else -10
        drawdown_analyzer.add_result(pnl)
    
    analyzer.compute_correlation_failures()
    analyzer.compute_market_pair_correlations()
    
    corr_metrics = analyzer.get_correlation_score()
    
    drawdown_metrics = {
        "max_drawdown": drawdown_analyzer.get_max_drawdown(),
        "tail_risk": drawdown_analyzer.get_tail_risk(),
    }
    
    win_streak, loss_streak = drawdown_analyzer.get_longest_streak()
    drawdown_metrics["longest_win_streak"] = win_streak
    drawdown_metrics["longest_loss_streak"] = loss_streak
    
    return {
        "correlation_metrics": corr_metrics,
        "drawdown_metrics": drawdown_metrics,
    }


def run_full_stress_test(historical_data: dict) -> dict:
    """Run complete stress test across all scenarios."""
    
    results = {}
    
    scenarios = [
        "baseline",
        "sudden_scoring_shift",
        "correlated_miscalibration",
        "prolonged_drift",
        "odds_bias_shift"
    ]
    
    for scenario in scenarios:
        if scenario == "baseline":
            data = historical_data
        elif scenario == "sudden_scoring_shift":
            data = StressTestScenarios.sudden_scoring_shift(historical_data.get("h2h", []))
        elif scenario == "correlated_miscalibration":
            data = StressTestScenarios.correlated_miscalibration(historical_data, ["btts", "ou25"])
        elif scenario == "prolonged_drift":
            data = StressTestScenarios.prolonged_drift(historical_data.get("h2h", []))
        elif scenario == "odds_bias_shift":
            data = StressTestScenarios.odds_bias_shift(historical_data.get("h2h", []))
        
        if isinstance(data, dict):
            combined = []
            for preds in data.values():
                combined.extend(preds)
            data = combined
        
        if data:
            results[scenario] = analyze_stress_results(data)
    
    return results