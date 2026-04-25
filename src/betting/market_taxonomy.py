import logging
from typing import Optional
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)

MARKET_FAMILIES = {
    "categorical_outcome": {
        "name": "Categorical Outcome",
        "target": "multiclass",
        "model_approaches": ["lightgbm", "xgboost"],
        "description": "Win/Draw/Loss classification"
    },
    "joint_event": {
        "name": "Joint Event (BTTS)",
        "target": "binary",
        "model_approaches": ["lightgbm", "logistic"],
        "description": "Both teams scoring - requires correlated event modeling"
    },
    "goal_distribution": {
        "name": "Goal Distribution",
        "target": "count",
        "model_approaches": ["poisson", "lightgbm"],
        "description": "Over/Under markets - Poisson-style count modeling"
    },
}


def get_market_info(market: str) -> Optional[dict]:
    """Get full taxonomy info for a market."""
    with get_session() as s:
        result = s.execute(
            text("""
                SELECT market, market_type, model_family, target_formulation, 
                       feature_set, calibration_family
                FROM market_taxonomy WHERE market = :market
            """),
            {"market": market}
        ).fetchone()
        
        if not result:
            logger.warning(f"No taxonomy for market: {market}")
            return None
        
        return {
            "market": result[0],
            "market_type": result[1],
            "model_family": result[2],
            "target_formulation": result[3],
            "feature_set": result[4],
            "calibration_family": result[5],
        }


def get_model_family(market: str) -> Optional[str]:
    """Get model family for a market."""
    info = get_market_info(market)
    return info["model_family"] if info else None


def get_target_formulation(market: str) -> Optional[str]:
    """Get target formulation for a market."""
    info = get_market_info(market)
    return info["target_formulation"] if info else None


def get_required_features(market: str) -> list[str]:
    """Get required feature set for a market."""
    info = get_market_info(market)
    if not info or not info["feature_set"]:
        return []
    return [f.strip() for f in info["feature_set"].split(",")]


def get_all_markets() -> list[str]:
    """Get all registered markets."""
    with get_session() as s:
        results = s.execute(text("SELECT market FROM market_taxonomy")).fetchall()
        return [r[0] for r in results]


def get_markets_by_family(family: str) -> list[str]:
    """Get all markets in a model family."""
    with get_session() as s:
        results = s.execute(
            text("SELECT market FROM market_taxonomy WHERE model_family = :family"),
            {"family": family}
        ).fetchall()
        return [r[0] for r in results]


def get_markets_by_type(mtype: str) -> list[str]:
    """Get all markets of a specific type."""
    with get_session() as s:
        results = s.execute(
            text("SELECT market FROM market_taxonomy WHERE market_type = :mtype"),
            {"mtype": mtype}
        ).fetchall()
        return [r[0] for r in results]


def is_market_registered(market: str) -> bool:
    """Check if market is in taxonomy."""
    return get_market_info(market) is not None


def explain_market_failure(market: str, feasibility_status: str, metrics: dict) -> str:
    """Provide detailed diagnosis for why a market is failing."""
    info = get_market_info(market)
    
    if not info:
        return f"UNKNOWN: Market {market} not in taxonomy"
    
    issues = []
    
    variance = metrics.get("variance", 0)
    if variance < 0.001:
        issues.append(f"CRITICAL: Probability collapse (variance={variance:.6f})")
        issues.append(f"  -> Model family '{info['model_family']}' may be wrong for this market type")
        issues.append(f"  -> Target formulation '{info['target_formulation']}' may not fit the problem")
    
    brier_raw = metrics.get("brier_raw", 0)
    brier_cal = metrics.get("brier_cal", brier_raw)
    if brier_cal >= brier_raw - 0.001:
        issues.append(f"  -> Calibration ineffective: Brier unchanged ({brier_raw:.4f} -> {brier_cal:.4f})")
        issues.append(f"  -> Calibration family '{info.get('calibration_family')}' may not suit the problem")
    
    corr = metrics.get("ev_roi_correlation", 0)
    if abs(corr) < 0.1:
        issues.append(f"  -> EV-ROI uncorrelated ({corr:.3f}): model not capturing predictive signal")
    
    if feasibility_status == "non-expressible":
        issues.append("DIAGNOSIS: Model family mismatch - current architecture cannot express this market")
        issues.append(f"  Current: {info['market_type']} / {info['model_family']}")
    
    if info["market_type"] == "joint_event":
        issues.append("  RECOMMEND: BTTS requires correlated event features (attack correlation, defensive holes)")
    
    if info["market_type"] == "goal_distribution":
        issues.append("  RECOMMEND: OU markets need xG-based distribution features, not just rank/goals")
    
    return "\n".join(issues) if issues else "No issues detected"


def get_model_family_requirements(family: str) -> dict:
    """Get requirements for a model family."""
    return MARKET_FAMILIES.get(family, {})