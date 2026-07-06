# src/models/ensemble.py - Weighted model blend
"""
Weighted ensemble that combines predictions from multiple models.

Optimizes weights based on cross-validated RPS performance.
"""
from __future__ import annotations

from typing import Protocol, Callable
import numpy as np
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture
from src.features.xg_features import get_injury_impact


class Predictor(Protocol):
    """Protocol for predictor models."""
    def predict_proba(self, home_team_id: int, away_team_id: int) -> tuple[float, float, float]: ...


@dataclass
class ModelWeight:
    """Weight and name for a model."""
    name: str
    weight: float
    predict_fn: Callable


class WeightedEnsemble:
    """
    Weighted ensemble of multiple models.
    
    Models are combined with optimized weights to minimize RPS.
    """
    
    def __init__(self):
        self._models: list[ModelWeight] = []
        self._fitted = False
    
    def add_model(
        self,
        name: str,
        weight: float,
        predict_fn: Callable,
    ) -> "WeightedEnsemble":
        """Add a model to the ensemble."""
        self._models.append(ModelWeight(name=name, weight=weight, predict_fn=predict_fn))
        return self
    
    def fit_weights(self, fixtures: list[Fixture]) -> "WeightedEnsemble":
        """Optimize weights to minimize RPS on validation set."""
        if len(self._models) == 0:
            return self
        
        # Get predictions from each model for each fixture
        predictions = []
        for model in self._models:
            preds = []
            for f in fixtures:
                if f.date is None or f.goals_home is None:
                    continue
                probs = model.predict_fn(f.home_team_id, f.away_team_id)
                preds.append(probs)
            predictions.append(np.array(preds))
        
        if not predictions[0].shape[0]:
            print("Warning: no valid fixtures for weight fitting")
            return self
        
        # Get actual outcomes
        actuals = []
        for f in fixtures:
            if f.date is None or f.goals_home is None:
                continue
            if f.goals_home > f.goals_away:
                actuals.append(0)
            elif f.goals_home == f.goals_away:
                actuals.append(1)
            else:
                actuals.append(2)
        actuals = np.array(actuals)
        
        # Optimize weights
        def rps_score(weights):
            weights = np.array(weights)
            weights = weights / weights.sum()  # Normalize
            
            combined = np.zeros((len(actuals), 3))
            for i, pred in enumerate(predictions):
                combined += weights[i] * pred
            
            # Calculate RPS
            total_rps = 0.0
            for j in range(len(actuals)):
                actual = actuals[j]
                for k in range(3):
                    cum_pred = sum(combined[j, :k+1])
                    cum_actual = 1.0 if actual <= k else 0.0
                    total_rps += (cum_pred - cum_actual) ** 2
            
            return total_rps / (3 * len(actuals))
        
        # Initial weights
        init_weights = [m.weight for m in self._models]
        
        # Optimize
        from scipy.optimize import minimize
        
        result = minimize(
            rps_score,
            init_weights,
            method="Nelder-Mead",
            options={"maxiter": 500},
        )
        
        optimized_weights = result.x
        optimized_weights = optimized_weights / optimized_weights.sum()
        
        # Update model weights
        for i, m in enumerate(self._models):
            m.weight = float(optimized_weights[i])
        
        print(f"Optimized weights: {[(m.name, f'{m.weight:.3f}') for m in self._models]}")
        print(f"Optimized RPS: {result.fun:.4f}")
        
        self._fitted = True
        return self
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        use_injuries: bool = True,
    ) -> tuple[float, float, float]:
        """Weighted average of model predictions."""
        if not self._models:
            return 0.33, 0.33, 0.34
        
        total_weight = sum(m.weight for m in self._models)
        if total_weight == 0:
            return 0.33, 0.33, 0.34
        
        combined = np.zeros(3)
        for m in self._models:
            probs = m.predict_fn(home_team_id, away_team_id)
            combined += m.weight * np.array(probs)
        
        combined /= total_weight
        
        # Apply injury adjustment if enabled
        if use_injuries:
            home_impact = get_injury_impact(home_team_id)
            away_impact = get_injury_impact(away_team_id)
            
            # Home advantage gets slight boost from opponent injuries
            if away_impact < 0:
                combined[0] += abs(away_impact) * 0.3  # Home win boost
                combined[1] += abs(away_impact) * 0.1  # Draw boost
            if home_impact < 0:
                combined[2] += abs(home_impact) * 0.3  # Away win boost
                
            # Renormalize
            combined /= combined.sum()
        
        return tuple(combined)
    
    def predict(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> str:
        """Predict match outcome."""
        probs = self.predict_proba(home_team_id, away_team_id)
        outcomes = ['H', 'D', 'A']
        return outcomes[np.argmax(probs)]


def rps_from_predictions(probs, actual):
    """Calculate RPS for a single prediction."""
    total = 0.0
    for i in range(3):
        cum_pred = sum(probs[:i+1])
        cum_actual = 1.0 if actual <= i else 0.0
        total += (cum_pred - cum_actual) ** 2
    return total / 3


def evaluate_model(model, fixtures: list[Fixture]) -> dict:
    """Evaluate a model on fixtures and return metrics."""
    total_rps = 0.0
    correct = 0
    total = 0
    
    for f in fixtures:
        if f.date is None or f.goals_home is None:
            continue
        
        try:
            probs = model.predict_proba(f.home_team_id, f.away_team_id)
            
            if f.goals_home > f.goals_away:
                actual = 0
            elif f.goals_home == f.goals_away:
                actual = 1
            else:
                actual = 2
            
            total_rps += rps_from_predictions(probs, actual)
            if np.argmax(probs) == actual:
                correct += 1
            total += 1
        except Exception as e:
            print(f"Error on fixture {f.id}: {e}")
    
    return {
        "rps": total_rps / total if total > 0 else 0,
        "accuracy": correct / total if total > 0 else 0,
        "n_matches": total,
    }