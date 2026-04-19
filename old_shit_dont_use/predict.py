"""
src/betting/predict.py

Unified prediction interface for all betting markets.
Provides a single entry point for model predictions.

Usage:
    from src.betting.predict import predict_proba

    probs = predict_proba("h2h", home_id, away_id)
    # Returns: {"1": 0.45, "X": 0.30, "2": 0.25}

    probs = predict_proba("btts", home_id, away_id)
    # Returns: {"Yes": 0.55, "No": 0.45}

    probs = predict_proba("ou25", home_id, away_id)
    # Returns: {"Over": 0.60, "Under": 0.40}

    # With uncertainty (Bayesian)
    result = predict_h2h_with_uncertainty(home_id, away_id)
    # Returns: {"1": 0.45, "X": 0.30, "2": 0.25, "uncertainty": 0.12, ...}
"""
from __future__ import annotations

from src.models.btts import predict_btts
from src.models.overunder import predict_ou25, predict_ou15
from src.models.halftime import predict_btts_1h, predict_btts_2h


def predict_proba(market: str, home_id: int, away_id: int, league_id: int | None = None) -> dict[str, float]:
    """
    Get model probabilities for a given market.

    Args:
        market: One of "h2h", "btts", "ou25", "ou15", "btts_1h", "btts_2h"
        home_id: Home team ID
        away_id: Away team ID
        league_id: Optional league ID for late goal adjustment

    Returns:
        Dict mapping outcome labels to probabilities
    """
    from src.models.late_goals import apply_late_goal_adjustment
    
    if market == "h2h":
        return predict_h2h(home_id, away_id)
    elif market == "btts":
        probs = predict_btts_std(home_id, away_id)
        # Apply late goal adjustment for BTTS
        if league_id:
            for k, v in probs.items():
                probs[k] = apply_late_goal_adjustment(v, league_id, "btts")
        return probs
    elif market == "ou25":
        probs = predict_ou25_std(home_id, away_id)
        if league_id:
            for k, v in probs.items():
                if "Over" in k:
                    probs[k] = apply_late_goal_adjustment(v, league_id, "over25")
                else:
                    probs[k] = apply_late_goal_adjustment(v, league_id, "under25")
        return probs
    elif market == "ou15":
        probs = predict_ou15_std(home_id, away_id)
        if league_id:
            for k, v in probs.items():
                if "Over" in k:
                    probs[k] = apply_late_goal_adjustment(v, league_id, "over15")
                else:
                    probs[k] = apply_late_goal_adjustment(v, league_id, "under15")
        return probs
    elif market == "btts_1h":
        return predict_btts_1h_std(home_id, away_id)
    elif market == "btts_2h":
        return predict_btts_2h_std(home_id, away_id)
    else:
        raise ValueError(f"Unknown market: {market}")


def predict_h2h(home_id: int, away_id: int) -> dict[str, float]:
    """Predict 1X2 match outcome. Returns {"1": home, "X": draw, "2": away}."""
    from scripts.web_ui import predict as predict_h2h_internal
    prob_home, prob_draw, prob_away = predict_h2h_internal(0, home_id, away_id)
    return {"1": prob_home, "X": prob_draw, "2": prob_away}


def predict_h2h_bayesian(home_id: int, away_id: int) -> dict[str, float]:
    """Predict 1X2 using Bayesian Dixon-Coles with calibrated probabilities."""
    from src.models.dixon_coles import predict_bayesian_h2h
    return predict_bayesian_h2h(home_id, away_id)


def predict_h2h_with_uncertainty(home_id: int, away_id: int) -> dict:
    """Predict 1X2 with Bayesian credible intervals. Returns probabilities + uncertainty."""
    from src.models.dixon_coles import predict_bayesian_h2h_with_uncertainty
    unc = predict_bayesian_h2h_with_uncertainty(home_id, away_id)
    return {
        "1": unc["P_home"],
        "X": unc["P_draw"],
        "2": unc["P_away"],
        "P_home_low": unc["P_home_low"],
        "P_home_high": unc["P_home_high"],
        "P_away_low": unc["P_away_low"],
        "P_away_high": unc["P_away_high"],
        "uncertainty": unc["uncertainty"],
    }


def predict_btts_std(home_id: int, away_id: int) -> dict[str, float]:
    """Predict BTTS. Returns {"Yes": both_score, "No": not_both}."""
    prob_yes, prob_no = predict_btts(home_id, away_id)
    return {"Yes": prob_yes, "No": prob_no}


def predict_ou25_std(home_id: int, away_id: int) -> dict[str, float]:
    """Predict Over/Under 2.5. Returns {"Over": over_25, "Under": under_25}."""
    prob_over, prob_under = predict_ou25(home_id, away_id)
    return {"Over": prob_over, "Under": prob_under}


def predict_ou15_std(home_id: int, away_id: int) -> dict[str, float]:
    """Predict Over/Under 1.5. Returns {"Over": over_15, "Under": under_15}."""
    prob_over, prob_under = predict_ou15(home_id, away_id)
    return {"Over": prob_over, "Under": prob_under}


def predict_btts_1h_std(home_id: int, away_id: int) -> dict[str, float]:
    """Predict BTTS First Half. Returns {"Yes": both_score_1h, "No": not_both_1h}."""
    prob_yes, prob_no = predict_btts_1h(home_id, away_id)
    return {"Yes": prob_yes, "No": prob_no}


def predict_btts_2h_std(home_id: int, away_id: int) -> dict[str, float]:
    """Predict BTTS Second Half. Returns {"Yes": both_score_2h, "No": not_both_2h}."""
    prob_yes, prob_no = predict_btts_2h(home_id, away_id)
    return {"Yes": prob_yes, "No": prob_no}


MARKET_OUTCOMES = {
    "h2h": ["1", "X", "2"],
    "btts": ["Yes", "No"],
    "ou25": ["Over", "Under"],
    "ou15": ["Over", "Under"],
    "btts_1h": ["Yes", "No"],
    "btts_2h": ["Yes", "No"],
}


def get_market_outcomes(market: str) -> list[str]:
    """Get list of outcomes for a market."""
    return MARKET_OUTCOMES.get(market, [])
