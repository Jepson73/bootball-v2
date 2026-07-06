import numpy as np
from typing import Dict, List, Tuple, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from datetime import datetime, timedelta
from collections import deque


class FootballRegime(Enum):
    NORMAL = "normal"
    HIGH_SCORING = "high_scoring"
    LOW_SCORING = "low_scoring"
    DEFENSIVE = "defensive"
    OFFENSIVE = "offensive"
    CHAOTIC = "chaotic"
    VAR_STRIKE = "var_strike"
    CRISIS = "crisis"


@dataclass
class LatentStateVector:
    """Unified latent state representing global football environment."""
    
    scoring_intensity: float = 0.0
    tactical_tempo: float = 0.0
    defensive_rigidity: float = 0.0
    referee_strictness: float = 0.0
    volatility: float = 0.0
    
    timestamp: Optional[datetime] = None
    regime: FootballRegime = FootballRegime.NORMAL
    confidence: float = 1.0
    
    def to_vector(self) -> np.ndarray:
        return np.array([
            self.scoring_intensity,
            self.tactical_tempo,
            self.defensive_rigidity,
            self.referee_strictness,
            self.volatility
        ])
    
    @classmethod
    def from_vector(cls, vec: np.ndarray, regime: FootballRegime = FootballRegime.NORMAL) -> 'LatentStateVector':
        if len(vec) != 5:
            raise ValueError("State vector must have 5 dimensions")
        return cls(
            scoring_intensity=vec[0],
            tactical_tempo=vec[1],
            defensive_rigidity=vec[2],
            referee_strictness=vec[3],
            volatility=vec[4],
            regime=regime
        )
    
    def regime_from_state(self) -> FootballRegime:
        s, t, d, r, v = self.to_vector()
        
        if v > 1.5:
            return FootballRegime.CHAOTIC
        if d > 1.0 and s < -0.5:
            return FootballRegime.DEFENSIVE
        if t > 1.0 and s > 0.5:
            return FootballRegime.OFFENSIVE
        if s > 1.0:
            return FootballRegime.HIGH_SCORING
        if s < -1.0:
            return FootballRegime.LOW_SCORING
        if r > 1.0:
            return FootballRegime.VAR_STRIKE
        
        return FootballRegime.NORMAL
    
    def distance_to(self, other: 'LatentStateVector') -> float:
        return float(np.linalg.norm(self.to_vector() - other.to_vector()))
    
    def apply_perturbation(self, perturbation: np.ndarray) -> 'LatentStateVector':
        new_vec = self.to_vector() + perturbation
        new_vec = np.clip(new_vec, -3, 3)
        new_regime = FootballRegime.NORMAL
        return LatentStateVector.from_vector(new_vec, new_regime)


@dataclass
class ObservedMatch:
    """Observable match features for latent state inference."""
    
    league_id: int
    home_goals: int
    away_goals: int
    total_goals: int
    btts: bool
    shots: int
    shots_on_target: int
    home_xg: float
    away_xg: float
    yellow_cards: int
    red_cards: int
    timestamp: datetime
    
    @property
    def goal_rate(self) -> float:
        return self.total_goals / 2.0
    
    @property
    def xg_total(self) -> float:
        return self.home_xg + self.away_xg
    
    @property
    def conversion_rate(self) -> float:
        if self.shots > 0:
            return self.total_goals / self.shots
        return 0.0


class LatentStateEvolution:
    """Markov process for latent state evolution over time."""
    
    def __init__(self, transition_variance: float = 0.1, random_walk_variance: float = 0.05):
        self.transition_variance = transition_variance
        self.random_walk_variance = random_walk_variance
        
        self.transition_matrix = self._build_transition_matrix()
        
    def _build_transition_matrix(self) -> Dict[Tuple[FootballRegime, FootballRegime], float]:
        return {
            (FootballRegime.NORMAL, FootballRegime.NORMAL): 0.80,
            (FootballRegime.NORMAL, FootballRegime.HIGH_SCORING): 0.05,
            (FootballRegime.NORMAL, FootballRegime.LOW_SCORING): 0.05,
            (FootballRegime.NORMAL, FootballRegime.CHAOTIC): 0.05,
            (FootballRegime.NORMAL, FootballRegime.DEFENSIVE): 0.05,
            (FootballRegime.HIGH_SCORING, FootballRegime.NORMAL): 0.70,
            (FootballRegime.HIGH_SCORING, FootballRegime.OFFENSIVE): 0.20,
            (FootballRegime.HIGH_SCORING, FootballRegime.CHAOTIC): 0.10,
            (FootballRegime.LOW_SCORING, FootballRegime.NORMAL): 0.70,
            (FootballRegime.LOW_SCORING, FootballRegime.DEFENSIVE): 0.20,
            (FootballRegime.LOW_SCORING, FootballRegime.CRISIS): 0.10,
            (FootballRegime.DEFENSIVE, FootballRegime.NORMAL): 0.75,
            (FootballRegime.DEFENSIVE, FootballRegime.LOW_SCORING): 0.20,
            (FootballRegime.DEFENSIVE, FootballRegime.CRISIS): 0.05,
            (FootballRegime.OFFENSIVE, FootballRegime.NORMAL): 0.75,
            (FootballRegime.OFFENSIVE, FootballRegime.HIGH_SCORING): 0.20,
            (FootballRegime.CHAOTIC, FootballRegime.NORMAL): 0.40,
            (FootballRegime.CHAOTIC, FootballRegime.HIGH_SCORING): 0.30,
            (FootballRegime.CHAOTIC, FootballRegime.LOW_SCORING): 0.30,
            (FootballRegime.CRISIS, FootballRegime.NORMAL): 0.30,
            (FootballRegime.CRISIS, FootballRegime.LOW_SCORING): 0.35,
            (FootballRegime.CRISIS, FootballRegime.DEFENSIVE): 0.35,
            (FootballRegime.VAR_STRIKE, FootballRegime.NORMAL): 0.70,
            (FootballRegime.VAR_STRIKE, FootballRegime.CRISIS): 0.30,
        }
    
    def predict_next_state(
        self, 
        current_state: LatentStateVector,
        regime_prior: Optional[Dict[FootballRegime, float]] = None
    ) -> LatentStateVector:
        current_vec = current_state.to_vector()
        
        regime = current_state.regime
        
        if regime_prior:
            regimes, probs = zip(*regime_prior.items())
            regime = np.random.choice(regimes, p=probs)
        else:
            transitions = [
                (next_regime, prob)
                for (current_regime, next_regime), prob in self.transition_matrix.items()
                if current_regime == regime
            ]
            if transitions:
                regimes, probs = zip(*transitions)
                regime = np.random.choice(regimes, p=probs)
        
        regime_effect = self._regime_to_vector_effect(regime)
        noise = np.random.normal(0, self.random_walk_variance, 5)
        
        next_vec = current_vec + regime_effect * 0.1 + noise
        next_vec = np.clip(next_vec, -3, 3)
        
        return LatentStateVector.from_vector(next_vec, regime)
    
    def _regime_to_vector_effect(self, regime: FootballRegime) -> np.ndarray:
        effects = {
            FootballRegime.NORMAL: np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
            FootballRegime.HIGH_SCORING: np.array([0.5, 0.3, -0.2, 0.0, 0.2]),
            FootballRegime.LOW_SCORING: np.array([-0.5, -0.3, 0.2, 0.0, 0.1]),
            FootballRegime.DEFENSIVE: np.array([-0.4, -0.4, 0.5, 0.1, 0.0]),
            FootballRegime.OFFENSIVE: np.array([0.4, 0.4, -0.3, 0.0, 0.2]),
            FootballRegime.CHAOTIC: np.array([0.2, 0.2, -0.2, 0.2, 0.8]),
            FootballRegime.VAR_STRIKE: np.array([0.0, 0.0, 0.0, 0.8, 0.3]),
            FootballRegime.CRISIS: np.array([-0.6, -0.5, 0.4, 0.3, 0.5]),
        }
        return effects.get(regime, np.zeros(5))
    
    def apply_latent_shock(
        self,
        current_state: LatentStateVector,
        shock_magnitude: float,
        shock_type: str = "random"
    ) -> LatentStateVector:
        if shock_type == "random":
            shock_direction = np.random.choice([-1, 1])
        elif shock_type == "high_scoring":
            shock_direction = 1
        elif shock_type == "low_scoring":
            shock_direction = -1
        elif shock_type == "defensive":
            shock_direction = -1
            shock_magnitude *= 1.5
        elif shock_type == "offensive":
            shock_direction = 1
            shock_magnitude *= 1.5
        elif shock_type == "chaotic":
            shock_direction = np.random.choice([-1, 1])
            shock_magnitude *= 2
        else:
            shock_direction = 0
        
        shock_vector = np.array([
            shock_direction * shock_magnitude * 0.4,
            shock_direction * shock_magnitude * 0.2,
            -shock_direction * shock_magnitude * 0.3,
            shock_direction * shock_magnitude * 0.3,
            shock_magnitude * 0.5
        ])
        
        new_vec = current_state.to_vector() + shock_vector
        new_vec = np.clip(new_vec, -3, 3)
        
        new_regime = FootballRegime.CHAOTIC if abs(shock_magnitude) > 0.5 else current_state.regime
        
        return LatentStateVector.from_vector(new_vec, new_regime)


class LatentStateInference:
    """Infer latent state from observed match data (HMM-style filtering)."""
    
    def __init__(self, history_window: int = 50):
        self.history_window = history_window
        self.observations = deque(maxlen=history_window)
        self.state_history: List[LatentStateVector] = []
        
        self.emission_mean = {
            'scoring_intensity': 0.0,
            'tactical_tempo': 0.0,
            'defensive_rigidity': 0.0,
            'referee_strictness': 0.0,
            'volatility': 0.0
        }
        
        self.emission_std = {
            'scoring_intensity': 0.5,
            'tactical_tempo': 0.4,
            'defensive_rigidity': 0.4,
            'referee_strictness': 0.3,
            'volatility': 0.3
        }
    
    def add_observation(self, match: ObservedMatch):
        self.observations.append(match)
        
        if len(self.observations) >= 5:
            self._update_state_history()
    
    def _update_state_history(self):
        if len(self.observations) < 5:
            return
        
        recent = list(self.observations)[-self.history_window:]
        
        goals = [m.goal_rate for m in recent]
        xgs = [m.xg_total / 4.0 for m in recent]
        btts_rate = sum(1 for m in recent if m.btts) / len(recent)
        card_rates = [(m.yellow_cards + m.red_cards * 2) / 2.0 for m in recent]
        shots = [m.shots / 20.0 for m in recent]
        
        scoring_intensity = np.mean(goals) * 2 - 1.0
        scoring_intensity = np.clip(scoring_intensity, -2, 2)
        
        tactical_tempo = np.mean(shots) - 0.5
        tactical_tempo = np.clip(tactical_tempo, -2, 2)
        
        defensive_rigidity = 1.0 - np.mean(goals) / 2.5
        defensive_rigidity = np.clip(defensive_rigidity, -2, 2)
        
        referee_strictness = np.mean(card_rates) - 0.5
        referee_strictness = np.clip(referee_strictness, -2, 2)
        
        goal_std = np.std(goals)
        xg_std = np.std(xgs)
        volatility = min(goal_std, xg_std) * 3
        volatility = np.clip(volatility, 0, 2)
        
        inferred_state = LatentStateVector(
            scoring_intensity=scoring_intensity,
            tactical_tempo=tactical_tempo,
            defensive_rigidity=defensive_rigidity,
            referee_strictness=referee_strictness,
            volatility=volatility,
            timestamp=recent[-1].timestamp,
            confidence=min(1.0, len(recent) / 20.0)
        )
        
        inferred_state.regime = inferred_state.regime_from_state()
        self.state_history.append(inferred_state)
    
    def get_current_state(self) -> Optional[LatentStateVector]:
        if not self.state_history:
            return None
        return self.state_history[-1]
    
    def get_state_prior(self) -> Dict[FootballRegime, float]:
        if len(self.state_history) < 3:
            return {FootballRegime.NORMAL: 1.0}
        
        recent = self.state_history[-10:]
        regime_counts = {}
        for state in recent:
            regime_counts[state.regime] = regime_counts.get(state.regime, 0) + 1
        
        total = len(recent)
        return {r: c / total for r, c in regime_counts.items()}
    
    def predict_next_state(
        self,
        evolution_model: LatentStateEvolution
    ) -> Optional[LatentStateVector]:
        current = self.get_current_state()
        if current is None:
            return None
        
        regime_prior = self.get_state_prior()
        return evolution_model.predict_next_state(current, regime_prior)


class MarketCoupling:
    """Link market outputs to shared latent state."""
    
    def __init__(self):
        self.market_sensitivities = {
            'h2h': {
                'scoring_intensity': 0.3,
                'tactical_tempo': 0.2,
                'defensive_rigidity': -0.3,
                'referee_strictness': 0.1,
                'volatility': 0.4
            },
            'btts': {
                'scoring_intensity': 0.5,
                'tactical_tempo': 0.4,
                'defensive_rigidity': -0.4,
                'referee_strictness': 0.2,
                'volatility': 0.3
            },
            'ou25': {
                'scoring_intensity': 0.6,
                'tactical_tempo': 0.3,
                'defensive_rigidity': -0.5,
                'referee_strictness': 0.1,
                'volatility': 0.2
            },
            'ou15': {
                'scoring_intensity': 0.4,
                'tactical_tempo': 0.2,
                'defensive_rigidity': -0.3,
                'referee_strictness': 0.1,
                'volatility': 0.2
            }
        }
    
    def apply_latent_state_to_market(
        self,
        market: str,
        base_predictions: List[Tuple],
        latent_state: LatentStateVector
    ) -> List[Tuple]:
        if market not in self.market_sensitivities:
            return base_predictions
        
        sensitivities = self.market_sensitivities[market]
        state_vec = latent_state.to_vector()
        
        sensitivity_vec = np.array([
            sensitivities['scoring_intensity'],
            sensitivities['tactical_tempo'],
            sensitivities['defensive_rigidity'],
            sensitivities['referee_strictness'],
            sensitivities['volatility']
        ])
        
        influence = np.dot(state_vec, sensitivity_vec)
        
        adjusted = []
        for pred in base_predictions:
            if len(pred) >= 3:
                predicted_correct, actual_correct, ev = pred[0], pred[1], pred[2]
                
                corruption = influence * 0.15
                new_ev = ev * (1 + corruption)
                
                error_prob = abs(influence) * 0.1
                if np.random.random() < error_prob:
                    actual_correct = not actual_correct
                
                adjusted.append((predicted_correct, actual_correct, new_ev))
            else:
                adjusted.append(pred)
        
        return adjusted
    
    def get_market_correlation_matrix(
        self,
        latent_state: LatentStateVector
    ) -> Dict[Tuple[str, str], float]:
        correlations = {}
        markets = list(self.market_sensitivities.keys())
        
        for i, m1 in enumerate(markets):
            for m2 in markets[i+1:]:
                s1 = np.array([
                    self.market_sensitivities[m1]['scoring_intensity'],
                    self.market_sensitivities[m1]['tactical_tempo'],
                    self.market_sensitivities[m1]['defensive_rigidity'],
                    self.market_sensitivities[m1]['referee_strictness'],
                    self.market_sensitivities[m1]['volatility']
                ])
                s2 = np.array([
                    self.market_sensitivities[m2]['scoring_intensity'],
                    self.market_sensitivities[m2]['tactical_tempo'],
                    self.market_sensitivities[m2]['defensive_rigidity'],
                    self.market_sensitivities[m2]['referee_strictness'],
                    self.market_sensitivities[m2]['volatility']
                ])
                
                corr = np.dot(s1, s2)
                correlations[(m1, m2)] = float(corr)
        
        return correlations


class UnifiedLatentModel:
    """Unified latent football state model that influences all markets."""
    
    def __init__(self, seed: int = 42):
        self.seed = seed
        np.random.seed(seed)
        
        self.inference = LatentStateInference()
        self.evolution = LatentStateEvolution()
        self.coupling = MarketCoupling()
        
        self.current_state = LatentStateVector(
            scoring_intensity=0.0,
            tactical_tempo=0.0,
            defensive_rigidity=0.0,
            referee_strictness=0.0,
            volatility=0.0,
            regime=FootballRegime.NORMAL
        )
        
        self.state_history: List[LatentStateVector] = []
        
    def update_from_match(self, match: ObservedMatch):
        self.inference.add_observation(match)
        
        inferred = self.inference.get_current_state()
        if inferred:
            self.current_state = inferred
            self.state_history.append(self.current_state)
    
    def apply_regime_shift(
        self,
        shift_type: str = "random",
        magnitude: float = 0.5
    ) -> LatentStateVector:
        self.current_state = self.evolution.apply_latent_shock(
            self.current_state, magnitude, shift_type
        )
        self.current_state.regime = self.current_state.regime_from_state()
        self.state_history.append(self.current_state)
        return self.current_state
    
    def apply_to_all_markets(
        self,
        predictions_by_market: Dict[str, List[Tuple]]
    ) -> Dict[str, List[Tuple]]:
        adjusted = {}
        
        for market, preds in predictions_by_market.items():
            adjusted[market] = self.coupling.apply_latent_state_to_market(
                market, preds, self.current_state
            )
        
        return adjusted
    
    def get_current_regime(self) -> FootballRegime:
        return self.current_state.regime
    
    def get_state_summary(self) -> Dict:
        return {
            "regime": self.current_state.regime.value,
            "scoring_intensity": self.current_state.scoring_intensity,
            "tactical_tempo": self.current_state.tactical_tempo,
            "defensive_rigidity": self.current_state.defensive_rigidity,
            "referee_strictness": self.current_state.referee_strictness,
            "volatility": self.current_state.volatility,
            "confidence": self.current_state.confidence,
            "state_vector": self.current_state.to_vector().tolist()
        }


def run_unified_latent_stress_test(
    historical_data: Dict[str, List[Tuple]],
    shift_scenarios: List[Tuple[str, float]] = None
) -> Dict:
    """Run stress test with unified latent state model."""
    
    if shift_scenarios is None:
        shift_scenarios = [
            ("random", 0.3),
            ("high_scoring", 0.5),
            ("low_scoring", 0.5),
            ("defensive", 0.4),
            ("offensive", 0.4),
            ("chaotic", 0.6),
        ]
    
    model = UnifiedLatentModel(seed=42)
    results = {}
    
    baseline_predictions = model.apply_to_all_markets(historical_data)
    results["baseline"] = {
        "predictions": baseline_predictions,
        "regime": model.get_current_regime().value,
        "state_summary": model.get_state_summary()
    }
    
    for shift_type, magnitude in shift_scenarios:
        model.current_state = LatentStateVector()
        
        model.apply_regime_shift(shift_type, magnitude)
        
        shifted_predictions = model.apply_to_all_markets(historical_data)
        
        results[shift_type] = {
            "predictions": shifted_predictions,
            "regime": model.get_current_regime().value,
            "state_summary": model.get_state_summary()
        }
    
    return results


def compute_latent_coupling_metrics(
    predictions_by_market: Dict[str, List[Tuple]],
    latent_model: UnifiedLatentModel
) -> Dict:
    """Compute metrics measuring system-wide coupling under latent state."""
    
    correlation_matrix = latent_model.coupling.get_market_correlation_matrix(
        latent_model.current_state
    )
    
    errors_by_market = {}
    for market, preds in predictions_by_market.items():
        if preds:
            errors = sum(1 for p in preds if not (p[1] if len(p) > 1 else True))
            errors_by_market[market] = errors / len(preds)
    
    total_errors = sum(errors_by_market.values())
    num_markets = len(errors_by_market)
    avg_error_rate = total_errors / num_markets if num_markets > 0 else 0
    
    variance_between = np.var(list(errors_by_market.values())) if errors_by_market else 0
    
    coupling_strength = 1.0 - min(1.0, variance_between * 10)
    
    return {
        "coupling_strength": coupling_strength,
        "average_error_rate": avg_error_rate,
        "error_variance_between_markets": variance_between,
        "market_correlations": correlation_matrix,
        "regime": latent_model.get_current_regime().value,
        "state_vector": latent_model.current_state.to_vector().tolist()
    }


def compare_independent_vs_unified(
    historical_data: Dict[str, List[Tuple]]
) -> Dict:
    """Compare independent shock model vs unified latent state model."""
    
    from src.betting.latent_shock import run_latent_shock_stress_test
    
    print("  Running independent shock model...")
    independent_results = run_latent_shock_stress_test(historical_data, num_shocks=5)
    
    print("  Running unified latent state model...")
    unified_results = run_unified_latent_stress_test(historical_data)
    
    independent_correlations = []
    for scenario, analysis in independent_results.items():
        sr = analysis.get("systemic_ratios", {})
        independent_correlations.append(sr.get("mean_correlation", 0))
    
    unified_model = UnifiedLatentModel(seed=42)
    unified_predictions = unified_model.apply_to_all_markets(historical_data)
    unified_metrics = compute_latent_coupling_metrics(unified_predictions, unified_model)
    
    return {
        "independent_model": {
            "avg_systemic_ratio": np.mean([
                a.get("systemic_ratios", {}).get("systemic_ratio", 0)
                for a in independent_results.values()
            ]),
            "avg_correlation": np.mean(independent_correlations) if independent_correlations else 0,
        },
        "unified_model": {
            "coupling_strength": unified_metrics.get("coupling_strength", 0),
            "error_variance": unified_metrics.get("error_variance_between_markets", 0),
            "regime": unified_metrics.get("regime", "unknown"),
        },
        "improvement": {
            "coupling_increase": unified_metrics.get("coupling_strength", 0),
        }
    }