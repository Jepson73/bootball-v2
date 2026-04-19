# src/models/ml_ensemble.py - XGBoost + LightGBM stacking
"""
ML ensemble using features from Phase 2.

Uses XGBoost and LightGBM with feature set:
- Form features
- Strength features  
- xG/shots features
- Elo ratings
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture

# Try to import ML libraries, fallback to sklearn if unavailable
try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV


@dataclass
class MatchFeatures:
    """Feature vector for a match."""
    home_form_5p: float
    home_form_10p: float
    home_win_rate_5: float
    home_goals_scored_5: float
    home_goals_conceded_5: float
    home_momentum: float
    home_days_rest: float
    away_form_5p: float
    away_form_10p: float
    away_win_rate_5: float
    away_goals_scored_5: float
    away_goals_conceded_5: float
    away_momentum: float
    away_days_rest: float
    form_diff_5p: float
    rest_diff: float
    home_attack: float
    home_defense: float
    away_attack: float
    away_defense: float
    attack_diff: float
    home_xg_for_5: float
    home_xg_against_5: float
    away_xg_for_5: float
    away_xg_against_5: float
    home_shots_on_target_5: float
    away_shots_on_target_5: float
    xg_diff_5: float
    elo_diff: float


def extract_features(fixture: Fixture) -> Optional[MatchFeatures]:
    """Extract features for a match using feature engines."""
    from src.features.form import FormEngine
    from src.features.strength import StrengthEngine
    from src.features.xg_features import XGEngine
    
    if not fixture.date:
        return None
    
    try:
        form_engine = FormEngine()
        strength_engine = StrengthEngine(league_id=fixture.league_id, season=fixture.season)
        xg_engine = XGEngine()
        
        # Need to fit strength engine first
        strength_engine.fit()
        
        # Get features
        form_feats = form_engine.get_features(
            fixture.home_team_id, 
            fixture.away_team_id,
            fixture.date
        )
        strength_feats = strength_engine.get_features(
            fixture.home_team_id,
            fixture.away_team_id
        )
        xg_feats = xg_engine.get_features(
            fixture.home_team_id,
            fixture.away_team_id,
            fixture.date
        )
        
        # Elo diff
        from src.features.elo import EloEngine
        elo_engine = EloEngine()
        elo_engine.fit()  # Load existing ratings
        
        # For now, just use 0 for missing ratings
        home_elo = elo_engine._get_current_rating(get_session().__enter__().__self__, fixture.home_team_id) if False else 0
        away_elo = 0
        
        return MatchFeatures(
            home_form_5p=form_feats.get("home_form_5p", 0),
            home_form_10p=form_feats.get("home_form_10p", 0),
            home_win_rate_5=form_feats.get("home_win_rate_5", 0),
            home_goals_scored_5=form_feats.get("home_goals_scored_5", 0),
            home_goals_conceded_5=form_feats.get("home_goals_conceded_5", 0),
            home_momentum=form_feats.get("home_momentum", 0),
            home_days_rest=form_feats.get("home_days_rest", 7),
            away_form_5p=form_feats.get("away_form_5p", 0),
            away_form_10p=form_feats.get("away_form_10p", 0),
            away_win_rate_5=form_feats.get("away_win_rate_5", 0),
            away_goals_scored_5=form_feats.get("away_goals_scored_5", 0),
            away_goals_conceded_5=form_feats.get("away_goals_conceded_5", 0),
            away_momentum=form_feats.get("away_momentum", 0),
            away_days_rest=form_feats.get("away_days_rest", 7),
            form_diff_5p=form_feats.get("form_diff_5p", 0),
            rest_diff=form_feats.get("rest_diff", 0),
            home_attack=strength_feats.get("home_attack", 0),
            home_defense=strength_feats.get("home_defense", 0),
            away_attack=strength_feats.get("away_attack", 0),
            away_defense=strength_feats.get("away_defense", 0),
            attack_diff=strength_feats.get("attack_diff", 0),
            home_xg_for_5=xg_feats.get("home_xg_for_5", 0),
            home_xg_against_5=xg_feats.get("home_xg_against_5", 0),
            away_xg_for_5=xg_feats.get("away_xg_for_5", 0),
            away_xg_against_5=xg_feats.get("away_xg_against_5", 0),
            home_shots_on_target_5=xg_feats.get("home_shots_on_target_5", 0),
            away_shots_on_target_5=xg_feats.get("away_shots_on_target_5", 0),
            xg_diff_5=xg_feats.get("xg_diff_5", 0),
            elo_diff=0,  # Would need proper Elo calculation
        )
    except Exception as e:
        print(f"Error extracting features for fixture {fixture.id}: {e}")
        return None


def features_to_array(f: MatchFeatures) -> np.ndarray:
    """Convert features to numpy array."""
    return np.array([
        f.home_form_5p, f.home_form_10p, f.home_win_rate_5,
        f.home_goals_scored_5, f.home_goals_conceded_5, f.home_momentum,
        f.home_days_rest,
        f.away_form_5p, f.away_form_10p, f.away_win_rate_5,
        f.away_goals_scored_5, f.away_goals_conceded_5, f.away_momentum,
        f.away_days_rest,
        f.form_diff_5p, f.rest_diff,
        f.home_attack, f.home_defense, f.away_attack, f.away_defense,
        f.attack_diff,
        f.home_xg_for_5, f.home_xg_against_5,
        f.away_xg_for_5, f.away_xg_against_5,
        f.home_shots_on_target_5, f.away_shots_on_target_5,
        f.xg_diff_5, f.elo_diff,
    ])


class MLEnsembleModel:
    """ML ensemble with XGBoost/LightGBM/RandomForest."""
    
    def __init__(self):
        self._models = []
        self._feature_names = [
            "home_form_5p", "home_form_10p", "home_win_rate_5",
            "home_goals_scored_5", "home_goals_conceded_5", "home_momentum",
            "home_days_rest",
            "away_form_5p", "away_form_10p", "away_win_rate_5",
            "away_goals_scored_5", "away_goals_conceded_5", "away_momentum",
            "away_days_rest",
            "form_diff_5p", "rest_diff",
            "home_attack", "home_defense", "away_attack", "away_defense",
            "attack_diff",
            "home_xg_for_5", "home_xg_against_5",
            "away_xg_for_5", "away_xg_against_5",
            "home_shots_on_target_5", "away_shots_on_target_5",
            "xg_diff_5", "elo_diff",
        ]
        self._is_fitted = False
    
    def fit(self, train_fixtures: list[Fixture]) -> "MLEnsembleModel":
        """Train ensemble on fixtures."""
        print("Extracting features...")
        
        X, y = [], []
        for f in train_fixtures:
            if f.goals_home is None or f.date is None:
                continue
            
            feats = extract_features(f)
            if feats is None:
                continue
            
            # Label: 0=home win, 1=draw, 2=away win
            if f.goals_home > f.goals_away:
                label = 0
            elif f.goals_home == f.goals_away:
                label = 1
            else:
                label = 2
            
            X.append(features_to_array(feats))
            y.append(label)
        
        X = np.array(X)
        y = np.array(y)
        
        print(f"Training on {len(X)} matches with {X.shape[1]} features")
        
        # Train models
        if HAS_XGB:
            xgb_model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                use_label_encoder=False,
                eval_metric="mlogloss",
                random_state=42,
            )
            xgb_model.fit(X, y)
            self._models.append(("XGBoost", xgb_model))
            print("  Trained XGBoost")
        
        if HAS_LGB:
            lgb_model = lgb.LGBMClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42,
                verbose=-1,
            )
            lgb_model.fit(X, y)
            self._models.append(("LightGBM", lgb_model))
            print("  Trained LightGBM")
        
        # Fallback to RandomForest
        rf_model = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            random_state=42,
        )
        rf_model.fit(X, y)
        self._models.append(("RandomForest", rf_model))
        print("  Trained RandomForest")
        
        self._is_fitted = True
        return self
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime,
    ) -> tuple[float, float, float]:
        """Predict probabilities by averaging model predictions."""
        if not self._is_fitted:
            raise ValueError("Model not fitted")
        
        # Create a dummy fixture to extract features
        from types import SimpleNamespace
        dummy = SimpleNamespace(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            date=match_date,
            league_id=39,
            season=2024,
        )
        
        feats = extract_features(dummy)
        if feats is None:
            return 0.33, 0.33, 0.34
        
        X = features_to_array(feats).reshape(1, -1)
        
        # Average predictions
        preds = []
        for name, model in self._models:
            proba = model.predict_proba(X)[0]
            preds.append(proba)
        
        avg_proba = np.mean(preds, axis=0)
        return tuple(avg_proba)
    
    def predict(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime,
    ) -> str:
        """Predict match outcome."""
        probs = self.predict_proba(home_team_id, away_team_id, match_date)
        outcomes = ['H', 'D', 'A']
        return outcomes[np.argmax(probs)]


def train_test_split(fixtures: list[Fixture], test_size: float = 0.2):
    """Split fixtures by time."""
    fixtures = sorted(fixtures, key=lambda f: f.date or datetime.min)
    split_idx = int(len(fixtures) * (1 - test_size))
    return fixtures[:split_idx], fixtures[split_idx:]