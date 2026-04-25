import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
from datetime import datetime
import os


class ShockType(Enum):
    SCORING_ENVIRONMENT = "scoring_environment"
    TACTICAL_META = "tactical_meta"
    REFEREE_STRICTNESS = "referee_strictness"
    BOOKMAKER_DRIFT = "bookmaker_drift"
    SEASONAL_FATIGUE = "seasonal_fatigue"
    WEATHER_SHOCK = "weather_shock"
    MARKET_SENTIMENT = "market_sentiment"


class RegimeState(Enum):
    NORMAL = "normal"
    HIGH_SCORING = "high_scoring"
    LOW_SCORING = "low_scoring"
    DEFENSIVE_ERA = "defensive_era"
    OFFENSIVE_ERA = "offensive_era"
    VAR_STRICT = "var_strict"
    VAR_LENIENT = "var_lenient"
    CRISIS = "crisis"


@dataclass
class LatentShock:
    shock_type: ShockType
    severity: float
    duration: int
    affected_markets: List[str]
    affected_leagues: List[int]
    propagation_factor: float
    description: str


@dataclass
class LatentRegime:
    regime_id: str
    active_shocks: List[LatentShock]
    scoring_multiplier: float
    btts_correlation: float
    ou25_correlation: float
    h2h_home_advantage: float
    volatility: float
    regime_state: RegimeState
    start_date: datetime
    end_date: Optional[datetime] = None


@dataclass
class MarketPerturbation:
    market: str
    probability_shift: float
    accuracy_degradation: float
    ev_corruption: float
    correlation_boost: float


class LatentShockGenerator:
    """Generate system-wide hidden shocks affecting multiple markets."""
    
    def __init__(self, seed: int = 42):
        self.seed = seed
        np.random.seed(seed)
        self.active_regimes: Dict[str, LatentRegime] = {}
        self.shock_history: List[Dict] = []
        self.regime_transition_probs = self._build_transition_matrix()
        
    def _build_transition_matrix(self) -> Dict[Tuple[RegimeState, RegimeState], float]:
        """Build Markov chain for regime transitions."""
        return {
            (RegimeState.NORMAL, RegimeState.NORMAL): 0.85,
            (RegimeState.NORMAL, RegimeState.HIGH_SCORING): 0.05,
            (RegimeState.NORMAL, RegimeState.LOW_SCORING): 0.05,
            (RegimeState.NORMAL, RegimeState.CRISIS): 0.05,
            (RegimeState.HIGH_SCORING, RegimeState.NORMAL): 0.70,
            (RegimeState.HIGH_SCORING, RegimeState.OFFENSIVE_ERA): 0.25,
            (RegimeState.HIGH_SCORING, RegimeState.CRISIS): 0.05,
            (RegimeState.LOW_SCORING, RegimeState.NORMAL): 0.70,
            (RegimeState.LOW_SCORING, RegimeState.DEFENSIVE_ERA): 0.25,
            (RegimeState.LOW_SCORING, RegimeState.CRISIS): 0.05,
            (RegimeState.CRISIS, RegimeState.NORMAL): 0.40,
            (RegimeState.CRISIS, RegimeState.LOW_SCORING): 0.30,
            (RegimeState.CRISIS, RegimeState.HIGH_SCORING): 0.30,
            (RegimeState.DEFENSIVE_ERA, RegimeState.NORMAL): 0.75,
            (RegimeState.DEFENSIVE_ERA, RegimeState.LOW_SCORING): 0.20,
            (RegimeState.OFFENSIVE_ERA, RegimeState.NORMAL): 0.75,
            (RegimeState.OFFENSIVE_ERA, RegimeState.HIGH_SCORING): 0.20,
        }
    
    def generate_scoring_environment_shock(
        self, 
        direction: str = "random",
        severity: float = 0.3
    ) -> LatentShock:
        """Generate global scoring environment shift."""
        
        if direction == "random":
            direction = np.random.choice(["high", "low"])
        
        direction_multiplier = 1.0 if direction == "high" else -1.0
        
        return LatentShock(
            shock_type=ShockType.SCORING_ENVIRONMENT,
            severity=severity * direction_multiplier,
            duration=np.random.randint(14, 42),
            affected_markets=["h2h", "btts", "ou25", "ou15"],
            affected_leagues=list(range(1, 100)),
            propagation_factor=0.8,
            description=f"Global scoring {direction} shift: {severity:.0%} goals change"
        )
    
    def generate_tactical_meta_shock(self, severity: float = 0.25) -> LatentShock:
        """Generate tactical meta change (defensive/offensive era)."""
        
        meta = np.random.choice(["defensive", "offensive"])
        
        return LatentShock(
            shock_type=ShockType.TACTICAL_META,
            severity=severity if meta == "defensive" else -severity,
            duration=np.random.randint(21, 56),
            affected_markets=["h2h", "btts", "ou25"],
            affected_leagues=list(range(1, 100)),
            propagation_factor=0.7,
            description=f"Tactical {meta} era shift"
        )
    
    def generate_referee_strictness_shock(self, severity: float = 0.2) -> LatentShock:
        """Generate referee/VAR strictness shift."""
        
        strictness = np.random.choice(["strict", "lenient"])
        
        return LatentShock(
            shock_type=ShockType.REFEREE_STRICTNESS,
            severity=severity if strictness == "strict" else -severity,
            duration=np.random.randint(14, 35),
            affected_markets=["h2h", "btts"],
            affected_leagues=list(range(1, 100)),
            propagation_factor=0.5,
            description=f"Referee strictness shift: {strictness}"
        )
    
    def generate_bookmaker_drift_shock(self, severity: float = 0.35) -> LatentShock:
        """Generate bookmaker model drift phase."""
        
        return LatentShock(
            shock_type=ShockType.BOOKMAKER_DRIFT,
            severity=severity,
            duration=np.random.randint(7, 21),
            affected_markets=["h2h", "btts", "ou25", "ou15"],
            affected_leagues=list(range(1, 100)),
            propagation_factor=0.9,
            description="Bookmaker odds drift phase"
        )
    
    def generate_seasonal_fatigue_shock(self, severity: float = 0.15) -> LatentShock:
        """Generate seasonal fatigue factor."""
        
        month = datetime.now().month
        is_winter = month in [12, 1, 2]
        is_summer = month in [6, 7, 8]
        
        direction = 1.0 if is_winter else (-0.5 if is_summer else 0.0)
        
        return LatentShock(
            shock_type=ShockType.SEASONAL_FATIGUE,
            severity=severity * direction,
            duration=14,
            affected_markets=["h2h", "btts", "ou25"],
            affected_leagues=[88, 94, 103, 157],
            propagation_factor=0.4,
            description="Seasonal fatigue factor"
        )
    
    def generate_compound_shock(self, num_shocks: int = 3) -> List[LatentShock]:
        """Generate multiple correlated shocks."""
        
        generators = [
            self.generate_scoring_environment_shock,
            self.generate_tactical_meta_shock,
            self.generate_referee_strictness_shock,
            self.generate_bookmaker_drift_shock,
            self.generate_seasonal_fatigue_shock,
        ]
        
        selected = np.random.choice(generators, size=min(num_shocks, len(generators)), replace=False)
        
        return [gen() for gen in selected]
    
    def apply_shock_to_predictions(
        self,
        predictions: List[Tuple],
        shock: LatentShock,
        market: str
    ) -> List[Tuple]:
        """Apply latent shock to prediction outcomes."""
        
        if market not in shock.affected_markets:
            return predictions
        
        np.random.seed(self.seed + hash(shock.shock_type.value) % 10000)
        
        perturbed = []
        
        for pred in predictions:
            if len(pred) >= 3:
                predicted_correct, actual_correct, ev = pred[0], pred[1], pred[2]
            else:
                continue
            
            if np.random.random() < abs(shock.severity):
                if shock.shock_type == ShockType.SCORING_ENVIRONMENT:
                    new_ev = ev + shock.severity * 0.5
                    actual_correct = not actual_correct if np.random.random() < 0.3 else actual_correct
                    
                elif shock.shock_type == ShockType.TACTICAL_META:
                    new_ev = ev + shock.severity * 0.3
                    actual_correct = not actual_correct if np.random.random() < 0.25 else actual_correct
                    
                elif shock.shock_type == ShockType.REFEREE_STRICTNESS:
                    new_ev = ev + shock.severity * 0.2
                    actual_correct = not actual_correct if np.random.random() < 0.2 else actual_correct
                    
                elif shock.shock_type == ShockType.BOOKMAKER_DRIFT:
                    new_ev = ev * (1 - shock.severity * 0.5)
                    actual_correct = not actual_correct if np.random.random() < 0.35 else actual_correct
                    
                elif shock.shock_type == ShockType.SEASONAL_FATIGUE:
                    new_ev = ev + shock.severity * 0.4
                    actual_correct = not actual_correct if np.random.random() < 0.15 else actual_correct
                else:
                    new_ev = ev
                
                perturbed.append((predicted_correct, actual_correct, new_ev))
            else:
                perturbed.append((predicted_correct, actual_correct, ev))
        
        return perturbed
    
    def get_regime_state(self) -> RegimeState:
        """Sample current regime state from transition matrix."""
        
        if not self.active_regimes:
            return RegimeState.NORMAL
        
        current = list(self.active_regimes.values())[-1]
        
        transitions = [
            (next_state, prob) 
            for (current_state, next_state), prob in self.regime_transition_probs.items()
            if current_state == current.regime_state
        ]
        
        if not transitions:
            return RegimeState.NORMAL
        
        states, probs = zip(*transitions)
        return np.random.choice(states, p=probs)
    
    def get_market_perturbations(
        self,
        regime: LatentRegime
    ) -> Dict[str, MarketPerturbation]:
        """Compute market-specific perturbations from active regime."""
        
        perturbations = {}
        
        base_prob_shift = regime.scoring_multiplier * regime.volatility
        base_acc_degradation = regime.volatility * 0.5
        
        perturbations["h2h"] = MarketPerturbation(
            market="h2h",
            probability_shift=base_prob_shift * 0.8,
            accuracy_degradation=base_acc_degradation * 0.7,
            ev_corruption=regime.volatility * regime.h2h_home_advantage,
            correlation_boost=regime.btts_correlation * 0.5
        )
        
        perturbations["btts"] = MarketPerturbation(
            market="btts",
            probability_shift=base_prob_shift * 1.2,
            accuracy_degradation=base_acc_degradation * 0.9,
            ev_corruption=regime.volatility * regime.btts_correlation,
            correlation_boost=regime.btts_correlation
        )
        
        perturbations["ou25"] = MarketPerturbation(
            market="ou25",
            probability_shift=base_prob_shift * 1.0,
            accuracy_degradation=base_acc_degradation * 0.8,
            ev_corruption=regime.volatility * regime.ou25_correlation,
            correlation_boost=regime.ou25_correlation
        )
        
        perturbations["ou15"] = MarketPerturbation(
            market="ou15",
            probability_shift=base_prob_shift * 0.7,
            accuracy_degradation=base_acc_degradation * 0.6,
            ev_corruption=regime.volatility * 0.3,
            correlation_boost=regime.ou25_correlation * 0.4
        )
        
        return perturbations


class SystemicFailureAnalyzer:
    """Analyze systemic failures across markets under latent shocks."""
    
    def __init__(self):
        self.market_pair_correlations: Dict[Tuple[str, str], List[float]] = {}
        self.failure_clusters: List[List[str]] = []
        self.systemic_events: List[Dict] = []
        
    def compute_cross_market_correlation(
        self,
        predictions_by_market: Dict[str, List[Tuple]]
    ) -> Dict[Tuple[str, str], float]:
        """Compute error correlation between market pairs."""
        
        correlations = {}
        markets = list(predictions_by_market.keys())
        
        for i, m1 in enumerate(markets):
            for m2 in markets[i+1:]:
                preds1 = predictions_by_market.get(m1, [])
                preds2 = predictions_by_market.get(m2, [])
                
                min_len = min(len(preds1), len(preds2))
                if min_len < 5:
                    continue
                
                errors1 = [0 if (p[1] if len(p) > 1 else True) else 1 for p in preds1[:min_len]]
                errors2 = [0 if (p[1] if len(p) > 1 else True) else 1 for p in preds2[:min_len]]
                
                if sum(errors1) > 1 and sum(errors2) > 1:
                    corr = np.corrcoef(errors1, errors2)[0, 1]
                    if not np.isnan(corr):
                        correlations[(m1, m2)] = float(corr)
                        self.market_pair_correlations.setdefault((m1, m2), []).append(float(corr))
        
        return correlations
    
    def detect_failure_clusters(
        self,
        predictions_by_market: Dict[str, List[Tuple]],
        threshold: float = 0.5
    ) -> List[List[str]]:
        """Detect which markets fail together."""
        
        error_rates = {}
        
        for market, preds in predictions_by_market.items():
            if preds:
                errors = sum(1 for p in preds if not (p[1] if len(p) > 1 else True))
                error_rates[market] = errors / len(preds)
        
        clusters = []
        
        high_error_markets = {
            m for m, rate in error_rates.items() 
            if rate > threshold
        }
        
        if len(high_error_markets) >= 2:
            clusters.append(list(high_error_markets))
        
        for market, rate in error_rates.items():
            if rate > threshold * 1.5:
                if not any(market in c for c in clusters):
                    clusters.append([market])
        
        self.failure_clusters = clusters
        return clusters
    
    def compute_systemic_vs_isolated_ratio(
        self,
        predictions_by_market: Dict[str, List[Tuple]]
    ) -> Dict[str, float]:
        """Compute ratio of systemic vs isolated failures."""
        
        correlations = self.compute_cross_market_correlation(predictions_by_market)
        
        if not correlations:
            return {"systemic_ratio": 0.0, "isolated_ratio": 1.0, "mixed_ratio": 0.0}
        
        strong_correlations = sum(1 for c in correlations.values() if c > 0.3)
        weak_correlations = sum(1 for c in correlations.values() if abs(c) <= 0.3)
        negative_correlations = sum(1 for c in correlations.values() if c < -0.3)
        
        total = len(correlations)
        
        return {
            "systemic_ratio": strong_correlations / max(1, total),
            "isolated_ratio": weak_correlations / max(1, total),
            "mixed_ratio": negative_correlations / max(1, total),
            "mean_correlation": float(np.mean(list(correlations.values()))) if correlations else 0.0,
            "max_correlation": float(max(correlations.values())) if correlations else 0.0,
            "min_correlation": float(min(correlations.values())) if correlations else 0.0,
        }
    
    def identify_fragility_zones(
        self,
        predictions_by_market: Dict[str, List[Tuple]],
        shock_scenarios: List[str]
    ) -> Dict:
        """Identify hidden fragility zones across market combinations."""
        
        fragility = {
            "fragile_combinations": [],
            "robust_combinations": [],
            "cascading_markets": [],
            "isolated_markets": [],
        }
        
        correlations = self.compute_cross_market_correlation(predictions_by_market)
        clusters = self.detect_failure_clusters(predictions_by_market)
        
        for (m1, m2), corr in correlations.items():
            if corr > 0.5:
                fragility["fragile_combinations"].append({
                    "markets": [m1, m2],
                    "correlation": corr,
                    "risk": "HIGH"
                })
            elif corr < -0.3:
                fragility["robust_combinations"].append({
                    "markets": [m1, m2],
                    "correlation": corr,
                    "risk": "LOW"
                })
        
        for cluster in clusters:
            if len(cluster) >= 2:
                fragility["cascading_markets"] = cluster
        
        markets = list(predictions_by_market.keys())
        for market in markets:
            all_corrs = [c for (m1, m2), c in correlations.items() if market in (m1, m2)]
            if all_corrs and max(all_corrs) < 0.2:
                fragility["isolated_markets"].append(market)
        
        return fragility
    
    def store_analysis(
        self,
        scenario: str,
        predictions_by_market: Dict[str, List[Tuple]],
        shock: Optional[LatentShock] = None
    ) -> Dict:
        """Store complete systemic failure analysis."""
        
        correlations = self.compute_cross_market_correlation(predictions_by_market)
        systemic_ratios = self.compute_systemic_vs_isolated_ratio(predictions_by_market)
        fragility = self.identify_fragility_zones(predictions_by_market, [scenario])
        
        analysis = {
            "scenario": scenario,
            "timestamp": datetime.now().isoformat(),
            "shock_description": shock.description if shock else "baseline",
            "shock_type": shock.shock_type.value if shock else "none",
            "shock_severity": shock.severity if shock else 0.0,
            "correlations": {f"{k[0]}_{k[1]}": v for k, v in correlations.items()},
            "systemic_ratios": systemic_ratios,
            "fragility_zones": fragility,
            "failure_clusters": self.failure_clusters,
        }
        
        self.systemic_events.append(analysis)
        return analysis


def run_latent_shock_stress_test(
    historical_data: Dict[str, List[Tuple[bool, bool, float]]],
    num_shocks: int = 5
) -> Dict:
    """Run complete latent shock stress test."""
    
    generator = LatentShockGenerator(seed=42)
    analyzer = SystemicFailureAnalyzer()
    
    results = {}
    
    scenarios = [
        ("baseline", None),
        ("scoring_high", generator.generate_scoring_environment_shock("high", 0.3)),
        ("scoring_low", generator.generate_scoring_environment_shock("low", 0.3)),
        ("defensive_era", generator.generate_tactical_meta_shock(0.25)),
        ("bookmaker_drift", generator.generate_bookmaker_drift_shock(0.35)),
        ("compound_shock", generator.generate_compound_shock(3)),
    ]
    
    for scenario_name, shock in scenarios:
        predictions_by_market = {}
        
        if shock is None:
            predictions_by_market = historical_data.copy()
        elif isinstance(shock, list):
            for market, preds in historical_data.items():
                perturbed = preds.copy()
                for s in shock:
                    perturbed = generator.apply_shock_to_predictions(perturbed, s, market)
                predictions_by_market[market] = perturbed
        else:
            for market, preds in historical_data.items():
                predictions_by_market[market] = generator.apply_shock_to_predictions(
                    preds, shock, market
                )
        
        analysis = analyzer.store_analysis(scenario_name, predictions_by_market)
        results[scenario_name] = analysis
    
    return results


def compute_portfolio_resilience_with_latent_shocks(
    historical_data: Dict[str, List[Tuple]]
) -> Dict:
    """Compute portfolio resilience including latent shock robustness."""
    
    generator = LatentShockGenerator(seed=42)
    analyzer = SystemicFailureAnalyzer()
    
    baseline = analyzer.compute_systemic_vs_isolated_ratio(historical_data)
    
    shock_results = run_latent_shock_stress_test(historical_data, num_shocks=5)
    
    shock_severities = []
    for scenario, analysis in shock_results.items():
        if scenario != "baseline":
            systemic_ratio = analysis.get("systemic_ratios", {}).get("systemic_ratio", 0)
            shock_severities.append(systemic_ratio)
    
    avg_shock_systemic = np.mean(shock_severities) if shock_severities else 0
    
    resilience = {
        "baseline_systemic_ratio": baseline.get("systemic_ratio", 0),
        "avg_shock_systemic_ratio": avg_shock_systemic,
        "latent_shock_robustness": 1.0 - min(1.0, avg_shock_systemic),
        "cross_market_stability": 1.0 - abs(baseline.get("mean_correlation", 0)),
        "scenario_results": {
            k: {
                "systemic_ratio": v.get("systemic_ratios", {}).get("systemic_ratio", 0),
                "fragile_combinations": len(v.get("fragility_zones", {}).get("fragile_combinations", []))
            }
            for k, v in shock_results.items()
        }
    }
    
    return resilience


def save_systemic_failure_analysis(
    analysis_results: Dict,
    output_dir: str = "/opt/projects/bootball/data"
) -> str:
    """Save systemic failure analysis to file for later retrieval."""
    
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"systemic_failure_analysis_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w') as f:
        json.dump(analysis_results, f, indent=2, default=str)
    
    return filepath


def load_latest_systemic_analysis(
    output_dir: str = "/opt/projects/bootball/data"
) -> Optional[Dict]:
    """Load the most recent systemic failure analysis."""
    
    pattern = "systemic_failure_analysis_*.json"
    import glob
    
    files = glob.glob(os.path.join(output_dir, pattern))
    if not files:
        return None
    
    latest = max(files, key=os.path.getctime)
    
    with open(latest, 'r') as f:
        return json.load(f)


def get_systemic_failure_summary(
    latent_results: Dict
) -> Dict:
    """Generate summary of systemic failure analysis."""
    
    summary = {
        "total_scenarios": len(latent_results),
        "scenarios": [],
        "highest_risk_scenario": None,
        "lowest_risk_scenario": None,
        "average_systemic_ratio": 0,
        "average_correlation": 0,
    }
    
    systemic_ratios = []
    correlations = []
    
    for scenario, analysis in latent_results.items():
        sr = analysis.get("systemic_ratios", {})
        systemic_ratios.append(sr.get("systemic_ratio", 0))
        correlations.append(sr.get("mean_correlation", 0))
        
        summary["scenarios"].append({
            "name": scenario,
            "systemic_ratio": sr.get("systemic_ratio", 0),
            "mean_correlation": sr.get("mean_correlation", 0),
            "shock_description": analysis.get("shock_description", ""),
            "fragile_combinations": len(analysis.get("fragility_zones", {}).get("fragile_combinations", [])),
            "isolated_markets": analysis.get("fragility_zones", {}).get("isolated_markets", []),
        })
    
    if systemic_ratios:
        summary["average_systemic_ratio"] = float(np.mean(systemic_ratios))
        summary["average_correlation"] = float(np.mean(correlations))
        
        highest_idx = np.argmax(systemic_ratios)
        lowest_idx = np.argmin(systemic_ratios)
        
        summary["highest_risk_scenario"] = list(latent_results.keys())[highest_idx]
        summary["lowest_risk_scenario"] = list(latent_results.keys())[lowest_idx]
    
    return summary