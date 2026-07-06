import logging
import os
import pickle
import sys
from pathlib import Path
import warnings

import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.storage.db import get_session
from src.storage.models import Standing, Fixture, FixtureStats
from src.betting.market_taxonomy import get_market_info, get_model_family

logger = logging.getLogger(__name__)

MODEL_PATH = '/opt/projects/bootball/data/model_{market}.pkl'

MARKET_OUTCOMES = {
    "h2h": ["1", "X", "2"],
    "btts": ["Yes", "No"],
    "ou25": ["Over", "Under"],
    "ou15": ["Over", "Under"],
}


def build_features_h2h(home: Standing, away: Standing, baseline=None) -> np.ndarray:
    """Build features for H2H (categorical outcome) — 9 features matching deployed LGBMClassifier models."""
    h_gf = float(home.goals_for or 1)
    h_ga = float(home.goals_against or 1)
    a_gf = float(away.goals_for or 1)
    a_ga = float(away.goals_against or 1)
    h_rank = float(home.rank or 15)
    a_rank = float(away.rank or 15)

    if baseline:
        avg_goals = baseline.avg_goals
        home_adv = baseline.home_advantage
        h_gf_n = (h_gf - avg_goals / 2 - home_adv / 2) / (avg_goals + 0.1)
        a_gf_n = (a_gf - avg_goals / 2 + home_adv / 2) / (avg_goals + 0.1)
        h_ga_n = (h_ga - avg_goals / 2 + home_adv / 2) / (avg_goals + 0.1)
        a_ga_n = (a_ga - avg_goals / 2 - home_adv / 2) / (avg_goals + 0.1)
        gd_home = (h_gf - h_ga - home_adv) / (avg_goals + 0.1)
        gd_away = (a_gf - a_ga + home_adv) / (avg_goals + 0.1)
    else:
        h_gf_n, a_gf_n, h_ga_n, a_ga_n = h_gf, a_gf, h_ga, a_ga
        gd_home = h_gf - h_ga
        gd_away = a_gf - a_ga

    return np.array([[
        h_rank,
        a_rank,
        gd_home,
        gd_away,
        h_gf_n,
        a_gf_n,
        h_ga_n,
        a_ga_n,
        float(abs(h_rank - a_rank)),
    ]])


def build_features_btts(home: Standing, away: Standing, baseline=None, fixture_id: int = None) -> np.ndarray:
    """Build features for BTTS (joint event) - attacking correlation features.
    
    If baseline is provided, features are normalized against league context.
    """
    home_gf = home.goals_for or 1
    home_ga = home.goals_against or 1
    away_gf = away.goals_for or 1
    away_ga = away.goals_against or 1
    
    if baseline:
        expected_btts = baseline.btts_rate
        avg_goals = baseline.avg_goals
        
        home_gf_norm = home_gf / (avg_goals + 0.1)
        away_gf_norm = away_gf / (avg_goals + 0.1)
        combined_attack_norm = (home_gf + away_gf) / (avg_goals * 2 + 0.1)
        
        attack_corr = (home_gf * away_gf) / max(home_gf + away_gf, 1) / (avg_goals + 0.1)
    else:
        home_gf_norm = home_gf
        away_gf_norm = away_gf
        combined_attack_norm = home_gf + away_gf
        attack_corr = (home_gf * away_gf) / max(home_gf + away_gf, 1)
    
    def_corr = (home_ga * away_ga) / max(home_ga + away_ga, 1)
    tempo = (home_gf + away_gf) / 2
    
    home_weak_def = home_ga / max(home_gf + home_ga, 1)
    away_weak_def = away_ga / max(away_gf + away_ga, 1)
    def_gaps = abs(home_weak_def - away_weak_def)
    
    return np.array([[
        attack_corr,
        def_corr,
        tempo,
        combined_attack_norm,
        home_weak_def,
        away_weak_def,
        def_gaps,
        home_gf_norm,
        away_gf_norm,
    ]])


def build_features_ou(home: Standing, away: Standing, over_threshold: float = 2.5, baseline=None) -> np.ndarray:
    """Build features for OU markets (goal distribution) - variance aware.
    
    If baseline is provided, features are normalized against league context.
    """
    home_gf = home.goals_for or 1
    home_ga = home.goals_against or 1
    away_gf = away.goals_for or 1
    away_ga = away.goals_against or 1
    
    if baseline:
        avg_goals = baseline.avg_goals
        expected_ou25 = baseline.ou25_rate
        
        home_gf_norm = home_gf / (avg_goals + 0.1)
        away_gf_norm = away_gf / (avg_goals + 0.1)
        
        total_expected = home_gf + away_gf
        total_expected_norm = (total_expected - avg_goals) / (avg_goals + 0.1)
        
        league_over_prob = expected_ou25
    else:
        home_gf_norm = home_gf
        away_gf_norm = away_gf
        total_expected = home_gf + away_gf
        total_expected_norm = total_expected
        league_over_prob = 0.5
    
    variance_proxy = abs(home_gf - away_gf) + abs(home_ga - away_ga)
    scoring_rate = (home_gf + away_gf) / (home_gf + home_ga + away_gf + away_ga + 1)
    
    home_scoring = home_gf / (home_gf + home_ga + 1)
    away_scoring = away_gf / (away_gf + away_ga + 1)
    
    over_prob_estimate = 1 / (1 + abs(over_threshold - total_expected))
    
    over_vs_league = over_prob_estimate - league_over_prob
    
    return np.array([[
        total_expected_norm,
        variance_proxy,
        scoring_rate,
        home_scoring,
        away_scoring,
        over_prob_estimate,
        home_gf_norm,
        away_gf_norm,
        over_vs_league,
    ]])


def build_features_for_market(market: str, home: Standing, away: Standing, fixture_id: int = None, league_id: int = None, use_rolling: bool = True) -> np.ndarray:
    """Build market-type specific features based on taxonomy.
    
    If league_id is provided, features are normalized against league baseline.
    If use_rolling is True (default), uses time-decayed rolling baselines with regime adjustment.
    """
    from src.betting.league_normalizer import get_league_baseline, get_default_baseline
    
    taxonomy = get_market_info(market)
    
    baseline = None
    if league_id:
        if use_rolling:
            from src.betting.temporal_adapter import get_regime_adjusted_baseline
            try:
                rolling = get_regime_adjusted_baseline(league_id)
                if rolling:
                    baseline = rolling
            except Exception:
                baseline = get_league_baseline(league_id)
        else:
            baseline = get_league_baseline(league_id)
    
    if baseline is None:
        baseline = get_default_baseline()
    
    if not taxonomy:
        logger.warning(f"No taxonomy for {market}, using H2H features")
        return build_features_h2h(home, away)
    
    market_type = taxonomy.get("market_type")
    
    if market_type == "categorical_outcome":
        return build_features_h2h(home, away, baseline)
    elif market_type == "joint_event":
        return build_features_btts(home, away, baseline, fixture_id)
    elif market_type == "goal_distribution":
        threshold = 1.5 if market == "ou15" else 2.5
        return build_features_ou(home, away, threshold, baseline)
    else:
        logger.warning(f"Unknown market type: {market_type}, using H2H features")
        return build_features_h2h(home, away, baseline)


def get_model_prediction(market: str, home_team_id: int, away_team_id: int, fixture_id: int = None, league_id: int = None) -> dict[str, float] | None:
    """Get prediction from trained model using market-type specific features.
    
    If league_id is provided, features are normalized against league baseline.

    Returns dict of outcome -> probability, or None if model unavailable.
    """
    model_path = MODEL_PATH.format(market=market)
    if not os.path.exists(model_path):
        logger.warning(f"Model not found: {model_path}")
        return None

    from src.security import safe_model_load
    obj = safe_model_load(model_path)

    if obj is None:
        logger.error(f"Failed to load model: {model_path}")
        return None

    try:
        if isinstance(obj, dict):
            model = obj['model']
            calibrator = obj.get('calibrator')
        else:
            model = obj
            calibrator = None

        with get_session() as s:
            home_standing = s.execute(
                select(Standing).where(Standing.team_id == home_team_id).where(Standing.season >= 2024)
            ).first()
            away_standing = s.execute(
                select(Standing).where(Standing.team_id == away_team_id).where(Standing.season >= 2024)
            ).first()

            if not home_standing or not away_standing:
                return None

            hs = home_standing[0]
            as_ = away_standing[0]

            features = build_features_for_market(market, hs, as_, fixture_id, league_id)

        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='X does not have valid feature names')
            raw_probs = model.predict_proba(features)[0]

        outcomes = MARKET_OUTCOMES.get(market, [])
        if len(outcomes) == 2:
            probs = {outcomes[0]: float(raw_probs[1]), outcomes[1]: float(1 - raw_probs[1])}
        elif len(raw_probs) == 3:
            probs = {outcomes[i]: float(raw_probs[i]) for i in range(3)}
        else:
            return None

        # Only apply MarketCalibrator (has a .calibrate() method); skip raw IsotonicRegression
        # objects embedded in older pkl files — they are fitted on a different scale and
        # produce 0 for out-of-range inputs, breaking the probability distribution.
        if calibrator and hasattr(calibrator, 'calibrate'):
            try:
                for k in probs:
                    probs[k] = calibrator.calibrate(probs[k]).calibrated_prob
            except Exception:
                pass

        return probs

    except Exception as e:
        logger.warning(f"Model prediction error for {market}: {e}")
        return None