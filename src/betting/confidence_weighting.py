import logging
import numpy as np
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)


@dataclass
class LayerConfidence:
    """Confidence score for a single inference layer."""
    layer_type: str
    confidence_score: float  # 0-1
    weight_applied: float   # Actual weight in final prediction
    sample_size: int
    variance: float
    metadata: Dict


def compute_baseline_confidence(league_id: int, window_matches: int = 50) -> LayerConfidence:
    """Compute confidence for rolling league baseline."""
    
    with get_session() as s:
        recent = s.execute(text("""
            SELECT goals_home, goals_away
            FROM fixtures
            WHERE league_id = :league_id AND status = 'FT'
            AND goals_home IS NOT NULL
            ORDER BY date DESC
            LIMIT :window
        """), {"league_id": league_id, "window": window_matches}).fetchall()
        
        if not recent or len(recent) < 10:
            return LayerConfidence(
                layer_type="league_baseline",
                confidence_score=0.1,
                weight_applied=0.1,
                sample_size=len(recent) if recent else 0,
                variance=999.0,
                metadata={"status": "insufficient_data"}
            )
        
        totals = [(r[0] or 0) + (r[1] or 0) for r in recent]
        mean_goals = np.mean(totals)
        variance = np.var(totals)
        
        sample_score = min(1.0, len(recent) / 30)
        stability_score = 1.0 / (1.0 + variance / 2)
        
        confidence = sample_score * 0.6 + stability_score * 0.4
        
        weight = confidence
        
        return LayerConfidence(
            layer_type="league_baseline",
            confidence_score=confidence,
            weight_applied=weight,
            sample_size=len(recent),
            variance=variance,
            metadata={
                "mean_goals": mean_goals,
                "window": window_matches,
            }
        )


def compute_regime_confidence(league_id: int) -> LayerConfidence:
    """Compute confidence for regime classification."""
    
    from src.betting.temporal_adapter import get_rolling_baseline, classify_regime
    
    rolling = get_rolling_baseline(league_id)
    if rolling is None:
        return LayerConfidence(
            layer_type="regime_classification",
            confidence_score=0.1,
            weight_applied=0.1,
            sample_size=0,
            variance=999.0,
            metadata={"status": "no_data"}
        )
    
    regime = classify_regime(rolling)
    
    confidence = regime.confidence
    sample_size = rolling.window_size
    
    sample_score = min(1.0, sample_size / 30)
    confidence_weighted = confidence * 0.7 + sample_score * 0.3
    
    weight = confidence_weighted * 0.5
    
    return LayerConfidence(
        layer_type="regime_classification",
        confidence_score=confidence_weighted,
        weight_applied=weight,
        sample_size=sample_size,
        variance=rolling.variance_goals,
        metadata={
            "regime_type": regime.regime_type,
            "is_volatile": regime.is_volatile,
        }
    )


def compute_drift_confidence(league_id: int, market: str) -> LayerConfidence:
    """Compute confidence for drift detection signal."""
    
    from src.betting.temporal_adapter import compute_drift_score, DRIFT_THRESHOLD
    
    drift = compute_drift_score(league_id, market)
    drift_score = drift.get("drift_score", 0)
    
    if drift.get("status") == "insufficient_data":
        return LayerConfidence(
            layer_type="drift_detection",
            confidence_score=0.1,
            weight_applied=0.0,
            sample_size=0,
            variance=999.0,
            metadata={"status": "insufficient_data"}
        )
    
    high_drift = drift_score > DRIFT_THRESHOLD
    
    if high_drift:
        confidence = 0.3
        weight = 0.2
    else:
        confidence = 0.7
        weight = 0.5
    
    return LayerConfidence(
        layer_type="drift_detection",
        confidence_score=confidence,
        weight_applied=weight,
        sample_size=25,
        variance=drift_score,
        metadata={
            "drift_score": drift_score,
            "status": drift.get("status"),
            "high_drift": high_drift,
        }
    )


def compute_model_confidence(market: str) -> LayerConfidence:
    """Compute confidence for model output based on historical performance."""
    
    with get_session() as s:
        preds = s.execute(text("""
            SELECT won, calibrated_prob
            FROM prediction_records
            WHERE market = :market AND settled = 1 AND won IS NOT NULL
            ORDER BY id DESC
            LIMIT 100
        """), {"market": market}).fetchall()
        
        if not preds or len(preds) < 20:
            return LayerConfidence(
                layer_type="model",
                confidence_score=0.5,
                weight_applied=0.8,
                sample_size=len(preds) if preds else 0,
                variance=0.25,
                metadata={"status": "low_sample"}
            )
        
        wins = sum(1 for p in preds if p[0])
        win_rate = wins / len(preds)
        
        cal_probs = [p[1] for p in preds if p[1]]
        if cal_probs:
            variance = np.var(cal_probs)
        else:
            variance = 0.25
        
        accuracy_score = 1.0 - abs(win_rate - 0.5) * 2
        sample_score = min(1.0, len(preds) / 50)
        
        confidence = accuracy_score * 0.4 + sample_score * 0.4 + (1 - variance) * 0.2
        weight = confidence * 0.9 + 0.1
        
        return LayerConfidence(
            layer_type="model",
            confidence_score=confidence,
            weight_applied=weight,
            sample_size=len(preds),
            variance=variance,
            metadata={
                "win_rate": win_rate,
                "accuracy_score": accuracy_score,
            }
        )


def combine_with_confidence(
    model_prob: float,
    baseline_adjustment: float,
    regime_adjustment: float,
    drift_correction: float,
    layers: Dict[str, LayerConfidence]
) -> tuple[float, Dict]:
    """Combine all adjustments using confidence-weighted inference.
    
    Returns final probability and weight breakdown.
    """
    
    raw_prediction = model_prob
    
    base_weight = layers.get("model", LayerConfidence("model", 0.5, 0.8, 0, 0, {}))
    
    league_weight = layers.get("league_baseline", LayerConfidence("league_baseline", 0.5, 0.5, 0, 0, {})).weight_applied
    regime_weight = layers.get("regime_classification", LayerConfidence("regime_classification", 0.5, 0.5, 0, 0, {})).weight_applied
    drift_weight = layers.get("drift_detection", LayerConfidence("drift_detection", 0.5, 0.5, 0, 0, {})).weight_applied
    
    total_adjustment = (
        baseline_adjustment * league_weight +
        regime_adjustment * regime_weight +
        drift_correction * drift_weight
    )
    
    total_weight = 1.0 + league_weight + regime_weight + drift_weight - 1.0
    
    normalized_adjustment = total_adjustment / total_weight if total_weight > 0 else 0
    
    final_prob = raw_prediction * base_weight.weight_applied + normalized_adjustment * (1 - base_weight.weight_applied)
    
    final_prob = np.clip(final_prob, 0.01, 0.99)
    
    breakdown = {
        "raw_prediction": raw_prediction,
        "baseline_adjustment": baseline_adjustment,
        "regime_adjustment": regime_adjustment,
        "drift_correction": drift_correction,
        "weights": {
            "model": base_weight.weight_applied,
            "league_baseline": league_weight,
            "regime": regime_weight,
            "drift": drift_weight,
        },
        "final_probability": final_prob,
    }
    
    return final_prob, breakdown


def compute_adjustments(league_id: int, market: str, model_prob: float) -> Dict[str, float]:
    """Compute adjustment factors for each layer."""
    
    from src.betting.league_normalizer import get_league_baseline
    from src.betting.temporal_adapter import get_rolling_baseline, classify_regime, compute_drift_score
    
    adjustments = {
        "baseline": 0.0,
        "regime": 0.0,
        "drift": 0.0,
    }
    
    league_baseline = get_league_baseline(league_id)
    rolling = get_rolling_baseline(league_id)
    
    if league_baseline and rolling:
        goals_diff = rolling.avg_goals - league_baseline.avg_goals
        adjustments["baseline"] = goals_diff / 5.0
    
    if rolling:
        regime = classify_regime(rolling)
        
        if "high_scoring" in regime.regime_type:
            adjustments["regime"] = 0.05
        elif "low_scoring" in regime.regime_type:
            adjustments["regime"] = -0.05
    
    drift = compute_drift_score(league_id, market)
    if drift.get("status") == "high_drift":
        adjustments["drift"] = -0.03
    
    return adjustments


def log_inference_confidence(
    fixture_id: int,
    market: str,
    layers: Dict[str, LayerConfidence],
    breakdown: Dict
) -> None:
    """Log inference confidence metadata to database."""
    
    try:
        with get_session() as s:
            for layer_type, layer in layers.items():
                s.execute(text("""
                    INSERT INTO inference_confidence_log
                    (fixture_id, market, layer_type, confidence_score, weight_applied, timestamp)
                    VALUES (:fixture_id, :market, :layer_type, :confidence, :weight, :timestamp)
                """), {
                    "fixture_id": fixture_id,
                    "market": market,
                    "layer_type": layer_type,
                    "confidence": layer.confidence_score,
                    "weight": layer.weight_applied,
                    "timestamp": datetime.utcnow().isoformat(),
                })
            s.commit()
    except Exception as e:
        logger.debug(f"Could not log confidence: {e}")


def create_confidence_log_table():
    """Create the inference_confidence_log table if it doesn't exist."""
    
    with get_session() as s:
        result = s.execute(text("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='inference_confidence_log'
        """)).fetchone()
        
        if not result:
            s.execute(text("""
                CREATE TABLE inference_confidence_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    market TEXT NOT NULL,
                    layer_type TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    weight_applied REAL NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            s.execute(text("""
                CREATE INDEX idx_confidence_fixture_market 
                ON inference_confidence_log(fixture_id, market)
            """))
            s.execute(text("""
                CREATE INDEX idx_confidence_timestamp 
                ON inference_confidence_log(timestamp)
            """))
            s.commit()
            logger.info("Created inference_confidence_log table")