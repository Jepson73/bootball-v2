import logging
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_MATCHES = 50
DEFAULT_DECAY_HALFLIFE = 0.8
DRIFT_THRESHOLD = 0.15
REGIME_CHANGE_THRESHOLD = 0.20


@dataclass
class RollingBaseline:
    """Time-decayed rolling baseline statistics."""
    league_id: int
    avg_goals: float
    btts_rate: float
    ou15_rate: float
    ou25_rate: float
    home_advantage: float
    variance_goals: float
    window_size: int
    period_days: int


@dataclass
class Regime:
    """League regime classification."""
    league_id: int
    regime_type: str
    confidence: float
    avg_goals: float
    variance: float
    is_volatile: bool


def compute_exponential_weights(n: int, halflife: float = 0.8) -> List[float]:
    """Compute exponential decay weights for time series.
    
    More recent items get higher weight.
    halflife = 0.8 means each match gets ~0.8x weight of the previous one
    """
    weights = []
    for i in range(n):
        weight = halflife ** i
        weights.append(weight)
    
    total = sum(weights)
    return [w / total for w in weights]


def get_rolling_baseline(league_id: int, window_matches: int = DEFAULT_WINDOW_MATCHES, 
                         halflife: float = DEFAULT_DECAY_HALFLIFE) -> Optional[RollingBaseline]:
    """Compute time-decayed rolling baseline for a league."""
    
    with get_session() as s:
        fixtures = s.execute(text("""
            SELECT 
                f.date,
                f.goals_home,
                f.goals_away
            FROM fixtures f
            WHERE f.league_id = :league_id
            AND f.status = 'FT'
            AND f.goals_home IS NOT NULL
            AND f.goals_away IS NOT NULL
            ORDER BY f.date DESC
            LIMIT :window
        """), {"league_id": league_id, "window": window_matches}).fetchall()
        
        if not fixtures or len(fixtures) < 10:
            return None
        
        n = len(fixtures)
        weights = compute_exponential_weights(n, halflife)
        
        goals_list = []
        btts_list = []
        ou15_list = []
        ou25_list = []
        home_adv_list = []
        
        for i, (date, gh, ga) in enumerate(fixtures):
            total = (gh or 0) + (ga or 0)
            btts = 1 if (gh or 0) > 0 and (ga or 0) > 0 else 0
            ou15 = 1 if total > 1.5 else 0
            ou25 = 1 if total > 2.5 else 0
            ha = (gh or 0) - (ga or 0)
            
            goals_list.append(total * weights[i])
            btts_list.append(btts * weights[i])
            ou15_list.append(ou15 * weights[i])
            ou25_list.append(ou25 * weights[i])
            home_adv_list.append(ha * weights[i])
        
        avg_goals = sum(goals_list)
        btts_rate = sum(btts_list)
        ou15_rate = sum(ou15_list)
        ou25_rate = sum(ou25_list)
        home_advantage = sum(home_adv_list)
        
        var_list = [(g - avg_goals) ** 2 for g in goals_list]
        variance_goals = sum([v * w for v, w in zip(var_list, weights)])
        
        first_date = fixtures[-1][0] if fixtures else None
        last_date = fixtures[0][0] if fixtures else None
        
        if isinstance(first_date, str):
            from datetime import datetime
            first_date = datetime.fromisoformat(first_date.replace('Z', '+00:00'))
            last_date = datetime.fromisoformat(last_date.replace('Z', '+00:00'))
        
        period_days = (last_date - first_date).days if first_date and last_date else 30
        
        return RollingBaseline(
            league_id=league_id,
            avg_goals=avg_goals,
            btts_rate=btts_rate,
            ou15_rate=ou15_rate,
            ou25_rate=ou25_rate,
            home_advantage=home_advantage,
            variance_goals=variance_goals,
            window_size=n,
            period_days=period_days,
        )


def classify_regime(rolling: RollingBaseline) -> Regime:
    """Classify current regime for a league based on rolling stats."""
    
    avg = rolling.avg_goals
    var = rolling.variance_goals
    
    if avg > 3.0:
        regime_type = "high_scoring"
        confidence = min(1.0, (avg - 3.0) / 1.0)
    elif avg < 2.3:
        regime_type = "low_scoring"
        confidence = min(1.0, (2.3 - avg) / 0.7)
    else:
        regime_type = "normal"
        confidence = 0.5
    
    is_volatile = var > 2.5
    
    if is_volatile:
        regime_type = f"{regime_type}_volatile" if regime_type != "normal" else "volatile"
        confidence += 0.2
    
    confidence = min(1.0, confidence)
    
    return Regime(
        league_id=rolling.league_id,
        regime_type=regime_type,
        confidence=confidence,
        avg_goals=avg,
        variance=var,
        is_volatile=is_volatile,
    )


def compute_drift_score(league_id: int, market: str, window_matches: int = DEFAULT_WINDOW_MATCHES) -> dict:
    """Compute drift metrics for a league + market combination."""
    
    with get_session() as s:
        half_window = window_matches // 2
        
        recent = s.execute(text("""
            SELECT f.goals_home, f.goals_away
            FROM fixtures f
            WHERE f.league_id = :league_id
            AND f.status = 'FT'
            AND f.goals_home IS NOT NULL
            ORDER BY f.date DESC
            LIMIT :window
        """), {"league_id": league_id, "window": half_window}).fetchall()
        
        older = s.execute(text("""
            SELECT f.goals_home, f.goals_away
            FROM fixtures f
            WHERE f.league_id = :league_id
            AND f.status = 'FT'
            AND f.goals_home IS NOT NULL
            ORDER BY f.date DESC
            LIMIT 1000
        """), {"league_id": league_id}).fetchall()
        
        if not recent or len(recent) < 10 or len(older) < 20:
            return {"drift_score": 0, "distribution_shift": 0, "baseline_change": 0, "status": "insufficient_data"}
        
        old_sample = older[half_window:half_window*2] if len(older) >= half_window * 2 else older
        if not old_sample:
            return {"drift_score": 0, "distribution_shift": 0, "baseline_change": 0, "status": "insufficient_data"}
        
        import numpy as np
        
        recent_goals = [(r[0] or 0) + (r[1] or 0) for r in recent]
        old_goals = [(o[0] or 0) + (o[1] or 0) for o in old_sample]
        
        recent_btts = sum(1 for r in recent if (r[0] or 0) > 0 and (r[1] or 0) > 0) / len(recent)
        old_btts = sum(1 for o in old_sample if (o[0] or 0) > 0 and (o[1] or 0) > 0) / len(old_sample)
        
        recent_ou25 = sum(1 for g in recent_goals if g > 2.5) / len(recent_goals)
        old_ou25 = sum(1 for g in old_goals if g > 2.5) / len(old_goals)
        
        recent_mean = np.mean(recent_goals)
        old_mean = np.mean(old_goals)
        
        distribution_shift = abs(recent_btts - old_btts) + abs(recent_ou25 - old_ou25)
        baseline_change = abs(recent_mean - old_mean) / (old_mean + 0.1)
        
        drift_score = (distribution_shift + baseline_change) / 2
        
        if drift_score > DRIFT_THRESHOLD:
            status = "high_drift"
        elif drift_score > DRIFT_THRESHOLD / 2:
            status = "moderate_drift"
        else:
            status = "stable"
        
        return {
            "drift_score": drift_score,
            "distribution_shift": distribution_shift,
            "baseline_change": baseline_change,
            "recent_btts": recent_btts,
            "old_btts": old_btts,
            "recent_ou25": recent_ou25,
            "old_ou25": old_ou25,
            "recent_avg_goals": recent_mean,
            "old_avg_goals": old_mean,
            "status": status,
        }


def get_regime_adjusted_baseline(league_id: int) -> RollingBaseline:
    """Get baseline adjusted for current regime."""
    
    rolling = get_rolling_baseline(league_id)
    if rolling is None:
        from src.betting.league_normalizer import get_default_baseline
        default = get_default_baseline()
        return RollingBaseline(
            league_id=league_id,
            avg_goals=default.avg_goals,
            btts_rate=default.btts_rate,
            ou15_rate=default.ou15_rate,
            ou25_rate=default.ou25_rate,
            home_advantage=default.home_advantage,
            variance_goals=default.variance_goals,
            window_size=50,
            period_days=30,
        )
    
    regime = classify_regime(rolling)
    
    if "high_scoring" in regime.regime_type:
        rolling.ou25_rate = min(0.9, rolling.ou25_rate * 1.1)
        rolling.btts_rate = min(0.9, rolling.btts_rate * 1.05)
    elif "low_scoring" in regime.regime_type:
        rolling.ou25_rate = max(0.2, rolling.ou25_rate * 0.9)
        rolling.btts_rate = max(0.3, rolling.btts_rate * 0.95)
    
    return rolling


def should_trigger_retrain(league_id: int, market: str) -> bool:
    """Check if drift warrants model retraining."""
    drift = compute_drift_score(league_id, market)
    return drift.get("drift_score", 0) > DRIFT_THRESHOLD * 1.5


def get_all_league_regimes() -> dict:
    """Get current regime for all leagues with sufficient data."""
    from src.betting.league_normalizer import get_all_league_baselines
    
    baselines = get_all_league_baselines()
    regimes = {}
    
    for league_id in baselines:
        rolling = get_rolling_baseline(league_id)
        if rolling:
            regimes[league_id] = classify_regime(rolling)
        else:
            regimes[league_id] = Regime(
                league_id=league_id,
                regime_type="unknown",
                confidence=0.0,
                avg_goals=2.5,
                variance=1.0,
                is_volatile=False,
            )
    
    return regimes