import numpy as np
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class LayerPerformance:
    """Performance metrics for a single layer."""
    
    layer_name: str
    ev_contribution: float = 0.0
    ev_contribution_pct: float = 0.0
    cumulative_ev: float = 0.0
    roi_contribution: float = 0.0
    variance_contribution: float = 0.0
    variance_reduction_pct: float = 0.0
    stability_score: float = 0.0
    activation_frequency: float = 0.0
    activation_count: int = 0
    rejection_impact: float = 0.0
    rejection_rate: float = 0.0
    bet_acceptance_rate: float = 0.0
    calibration_improvement: float = 0.0
    predictions_affected: int = 0
    decisions_changed: int = 0
    regime: str = "normal"


class LayerEvolutionEngine:
    """Analyzes layer behavior across runs over time."""
    
    def __init__(self):
        self.layers = [
            "calibration",
            "league_norm", 
            "latent_state",
            "drift",
            "risk"
        ]
    
    def compute_layer_stability_score(
        self,
        run_ids: List[str],
        layer_name: str
    ) -> float:
        """Compute stability score for a layer across runs."""
        
        from src.storage.db import get_session
        
        try:
            with get_session() as s:
                results = s.execute(text("""
                    SELECT ev_contribution, ev_contribution_pct, stability_score
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_name
                    ORDER BY run_id
                """), {"run_ids": ','.join(run_ids), "layer_name": layer_name}).fetchall()
                
                if not results:
                    return 0.5
                
                ev_values = [r.ev_contribution for r in results if r.ev_contribution is not None]
                
                if len(ev_values) < 2:
                    return 0.5
                
                mean_ev = np.mean(ev_values)
                std_ev = np.std(ev_values)
                
                if abs(mean_ev) < 0.001:
                    return 0.5
                
                cv = abs(std_ev / mean_ev) if mean_ev != 0 else 1.0
                
                stability = 1.0 / (1.0 + cv)
                
                consistency = sum(1 for e in ev_values if (e > 0) == (mean_ev > 0)) / len(ev_values)
                
                return stability * 0.7 + consistency * 0.3
                
        except Exception as e:
            logger.warning(f"Failed to compute stability score: {e}")
            return 0.5
    
    def compute_layer_utility_score(
        self,
        run_ids: List[str],
        layer_name: str
    ) -> float:
        """Compute utility score for a layer across runs."""
        
        from src.storage.db import get_session
        
        try:
            with get_session() as s:
                results = s.execute(text("""
                    SELECT 
                        AVG(ev_contribution) as avg_ev,
                        AVG(calibration_improvement) as avg_calib,
                        AVG(roi_contribution) as avg_roi,
                        SUM(predictions_affected) as total_affected,
                        SUM(decisions_changed) as total_changed,
                        COUNT(*) as run_count
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_name
                """), {"run_ids": ','.join(run_ids), "layer_name": layer_name}).fetchone()
                
                if not results or results.run_count == 0:
                    return 0.0
                
                ev_weight = 0.4
                calib_weight = 0.3
                roi_weight = 0.2
                change_weight = 0.1
                
                ev_score = results.avg_ev or 0.0
                calib_score = results.avg_calib or 0.0
                roi_score = results.avg_roi or 0.0
                
                change_rate = (results.total_changed or 0) / max(1, results.total_affected or 1)
                
                utility = (
                    ev_weight * ev_score +
                    calib_weight * calib_score +
                    roi_weight * roi_score +
                    change_weight * change_rate
                )
                
                return max(0.0, utility)
                
        except Exception as e:
            logger.warning(f"Failed to compute utility score: {e}")
            return 0.0
    
    def compute_layer_fragility_score(
        self,
        run_ids: List[str],
        layer_name: str
    ) -> float:
        """Compute fragility score for a layer."""
        
        from src.storage.db import get_session
        
        try:
            with get_session() as s:
                results = s.execute(text("""
                    SELECT 
                        AVG(CASE WHEN regime = 'normal' THEN ev_contribution ELSE NULL END) as normal_ev,
                        AVG(CASE WHEN regime != 'normal' THEN ev_contribution ELSE NULL END) as stress_ev,
                        AVG(CASE WHEN regime = 'drift' THEN ev_contribution ELSE NULL END) as drift_ev,
                        AVG(CASE WHEN regime = 'high_scoring' THEN ev_contribution ELSE NULL END) as high_ev,
                        AVG(CASE WHEN regime = 'low_scoring' THEN ev_contribution ELSE NULL END) as low_ev,
                        AVG(variance_contribution) as avg_var
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_name
                """), {"run_ids": ','.join(run_ids), "layer_name": layer_name}).fetchone()
                
                if not results:
                    return 0.5
                
                normal_ev = results.normal_ev or 0.0
                stress_ev = results.stress_ev or 0.0
                drift_ev = results.drift_ev or 0.0
                
                if normal_ev == 0:
                    return 0.5
                
                stress_degradation = abs(normal_ev - stress_ev) / abs(normal_ev) if normal_ev != 0 else 0
                drift_failure = abs(normal_ev - drift_ev) / abs(normal_ev) if normal_ev != 0 else 0
                
                variance_penalty = (results.avg_var or 0) * 0.5
                
                fragility = min(1.0, stress_degradation + drift_failure + variance_penalty)
                
                return fragility
                
        except Exception as e:
            logger.warning(f"Failed to compute fragility score: {e}")
            return 0.5
    
    def compute_layer_redundancy_score(
        self,
        run_ids: List[str],
        layer_name: str,
        all_layers: List[str]
    ) -> float:
        """Compute redundancy score by correlation with other layers."""
        
        from src.storage.db import get_session
        
        try:
            with get_session() as s:
                results = s.execute(text("""
                    SELECT run_id, ev_contribution
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_name
                    ORDER BY run_id
                """), {"run_ids": ','.join(run_ids), "layer_name": layer_name}).fetchall()
                
                if not results:
                    return 0.0
                
                layer_ev = [r.ev_contribution for r in results if r.ev_contribution is not None]
                
                if len(layer_ev) < 2:
                    return 0.0
                
                correlations = []
                
                for other_layer in all_layers:
                    if other_layer == layer_name:
                        continue
                    
                    other_results = s.execute(text("""
                        SELECT ev_contribution
                        FROM layer_performance_timeseries
                        WHERE run_id IN (:run_ids) AND layer_name = :other_layer
                        ORDER BY run_id
                    """), {"run_ids": ','.join(run_ids), "other_layer": other_layer}).fetchall()
                    
                    other_ev = [r.ev_contribution for r in other_results if r.ev_contribution is not None]
                    
                    min_len = min(len(layer_ev), len(other_ev))
                    if min_len >= 2:
                        corr = np.corrcoef(layer_ev[:min_len], other_ev[:min_len])[0, 1]
                        if not np.isnan(corr):
                            correlations.append(abs(corr))
                
                if not correlations:
                    return 0.0
                
                avg_correlation = np.mean(correlations)
                
                redundancy = min(1.0, avg_correlation)
                
                return redundancy
                
        except Exception as e:
            logger.warning(f"Failed to compute redundancy score: {e}")
            return 0.0
    
    def compute_layer_interaction(
        self,
        run_ids: List[str],
        layer_a: str,
        layer_b: str
    ) -> Dict:
        """Compute interaction metrics between two layers."""
        
        from src.storage.db import get_session
        
        try:
            with get_session() as s:
                results_a = s.execute(text("""
                    SELECT run_id, ev_contribution, decisions_changed, predictions_affected
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_a
                """), {"run_ids": ','.join(run_ids), "layer_a": layer_a}).fetchall()
                
                results_b = s.execute(text("""
                    SELECT run_id, ev_contribution, decisions_changed, predictions_affected
                    FROM layer_performance_timeseries
                    WHERE run_id IN (:run_ids) AND layer_name = :layer_b
                """), {"run_ids": ','.join(run_ids), "layer_b": layer_b}).fetchall()
                
                ev_a = [r.ev_contribution for r in results_a if r.ev_contribution is not None]
                ev_b = [r.ev_contribution for r in results_b if r.ev_contribution is not None]
                
                min_len = min(len(ev_a), len(ev_b))
                if min_len < 2:
                    return {"correlation": 0.0, "type": "insufficient_data"}
                
                correlation = np.corrcoef(ev_a[:min_len], ev_b[:min_len])[0, 1]
                
                if np.isnan(correlation):
                    correlation = 0.0
                
                joint_activation = 0
                total_runs = min_len
                
                for i in range(min_len):
                    a_activated = abs(ev_a[i]) > 0.01
                    b_activated = abs(ev_b[i]) > 0.01
                    if a_activated and b_activated:
                        joint_activation += 1
                
                joint_rate = joint_activation / total_runs if total_runs > 0 else 0
                
                if correlation > 0.6:
                    interaction_type = "reinforcing"
                elif correlation < -0.4:
                    interaction_type = "canceling"
                elif joint_rate < 0.1:
                    interaction_type = "independent"
                else:
                    interaction_type = "unstable"
                
                ev_synergy = 0
                for i in range(min_len):
                    if ev_a[i] > 0 and ev_b[i] > 0:
                        ev_synergy += ev_a[i] + ev_b[i]
                
                return {
                    "correlation": float(correlation),
                    "interaction_type": interaction_type,
                    "joint_activation_rate": joint_rate,
                    "ev_synergy": float(ev_synergy / total_runs)
                }
                
        except Exception as e:
            logger.warning(f"Failed to compute layer interaction: {e}")
            return {"correlation": 0.0, "type": "error"}


def compute_layer_evolution_metrics(
    run_ids: List[str],
    layers: List[str] = None
) -> Dict:
    """Compute comprehensive layer evolution metrics across runs."""
    
    if layers is None:
        layers = ["calibration", "league_norm", "latent_state", "drift", "risk"]
    
    engine = LayerEvolutionEngine()
    
    results = {
        "run_count": len(run_ids),
        "layers": {},
        "overall_trends": {}
    }
    
    for layer in layers:
        stability = engine.compute_layer_stability_score(run_ids, layer)
        utility = engine.compute_layer_utility_score(run_ids, layer)
        fragility = engine.compute_layer_fragility_score(run_ids, layer)
        redundancy = engine.compute_layer_redundancy_score(run_ids, layer, layers)
        
        results["layers"][layer] = {
            "stability_score": stability,
            "utility_score": utility,
            "fragility_score": fragility,
            "redundancy_score": redundancy,
            "composite_score": (utility * 0.4 + stability * 0.3 - fragility * 0.2 - redundancy * 0.1)
        }
    
    layer_scores = [(l, d["composite_score"]) for l, d in results["layers"].items()]
    layer_scores.sort(key=lambda x: x[1], reverse=True)
    
    results["overall_trends"]["best_layer"] = layer_scores[0][0] if layer_scores else None
    results["overall_trends"]["worst_layer"] = layer_scores[-1][0] if layer_scores else None
    
    return results


def compute_all_layer_interactions(run_ids: List[str]) -> Dict:
    """Compute interactions between all layer pairs."""
    
    layers = ["calibration", "league_norm", "latent_state", "drift", "risk"]
    engine = LayerEvolutionEngine()
    
    interactions = {}
    
    for i, layer_a in enumerate(layers):
        for layer_b in layers[i+1:]:
            key = f"{layer_a}_vs_{layer_b}"
            interactions[key] = engine.compute_layer_interaction(run_ids, layer_a, layer_b)
    
    return interactions


def generate_system_insights(
    run_ids: List[str],
    layer_metrics: Dict = None
) -> List[Dict]:
    """Generate actionable system insights based on layer analysis."""
    
    if layer_metrics is None:
        layer_metrics = compute_layer_evolution_metrics(run_ids)
    
    interactions = compute_all_layer_interactions(run_ids)
    
    insights = []
    
    for layer, metrics in layer_metrics.get("layers", {}).items():
        stability = metrics.get("stability_score", 0.5)
        utility = metrics.get("utility_score", 0)
        fragility = metrics.get("fragility_score", 0.5)
        redundancy = metrics.get("redundancy_score", 0)
        
        if fragility > 0.6:
            insights.append({
                "type": "fragility",
                "category": "fragility",
                "layer": layer,
                "text": f"{layer.title()} layer is fragile (score: {fragility:.2f}). "
                        f"It improves EV in normal conditions but fails under stress.",
                "confidence": fragility,
                "supporting_runs": len(run_ids)
            })
        
        if redundancy > 0.6:
            insights.append({
                "type": "redundancy",
                "category": "redundancy", 
                "layer": layer,
                "text": f"{layer.title()} layer is redundant (score: {redundancy:.2f}). "
                        f"Consider simplifying or removing it.",
                "confidence": redundancy,
                "supporting_runs": len(run_ids)
            })
        
        if stability > 0.8 and utility > 0.1:
            insights.append({
                "type": "valuable",
                "category": "utility",
                "layer": layer,
                "text": f"{layer.title()} layer is consistently valuable (stability: {stability:.2f}, utility: {utility:.2f}).",
                "confidence": stability * utility,
                "supporting_runs": len(run_ids)
            })
        
        if utility < 0.05 and stability < 0.4:
            insights.append({
                "type": "harmful",
                "category": "utility",
                "layer": layer,
                "text": f"{layer.title()} layer provides minimal value (utility: {utility:.2f}) and adds complexity.",
                "confidence": 1.0 - stability,
                "supporting_runs": len(run_ids)
            })
    
    reinforcing_pairs = []
    canceling_pairs = []
    
    for key, data in interactions.items():
        if data.get("interaction_type") == "reinforcing":
            reinforcing_pairs.append(key)
        elif data.get("interaction_type") == "canceling":
            canceling_pairs.append(key)
    
    if reinforcing_pairs:
        insights.append({
            "type": "reinforcing",
            "category": "interaction",
            "layer": None,
            "text": f"Reinforcing layer pairs: {', '.join(reinforcing_pairs)}",
            "confidence": 0.7,
            "supporting_runs": len(run_ids)
        })
    
    if canceling_pairs:
        insights.append({
            "type": "canceling",
            "category": "interaction", 
            "layer": None,
            "text": f"Canceling layer pairs (consider simplifying): {', '.join(canceling_pairs)}",
            "confidence": 0.7,
            "supporting_runs": len(run_ids)
        })
    
    return insights


def save_layer_performance_record(
    run_id: str,
    layer_name: str,
    performance: LayerPerformance
) -> None:
    """Save layer performance record to database."""
    
    from src.storage.db import get_session
    
    try:
        with get_session() as s:
            s.execute(text("""
                INSERT OR REPLACE INTO layer_performance_timeseries (
                    run_id, layer_name, ev_contribution, ev_contribution_pct,
                    cumulative_ev, roi_contribution, variance_contribution,
                    variance_reduction_pct, stability_score, activation_frequency,
                    activation_count, rejection_impact, rejection_rate,
                    bet_acceptance_rate, calibration_improvement,
                    predictions_affected, decisions_changed, regime
                ) VALUES (
                    :run_id, :layer_name, :ev_contrib, :ev_contrib_pct,
                    :cum_ev, :roi_contrib, :var_contrib,
                    :var_reduc_pct, :stability, :activation_freq,
                    :activation_count, :rejection_impact, :rejection_rate,
                    :bet_accept_rate, :calib_improvement,
                    :preds_affected, :decisions_changed, :regime
                )
            """), {
                "run_id": run_id,
                "layer_name": layer_name,
                "ev_contrib": performance.ev_contribution,
                "ev_contrib_pct": performance.ev_contribution_pct,
                "cum_ev": performance.cumulative_ev,
                "roi_contrib": performance.roi_contribution,
                "var_contrib": performance.variance_contribution,
                "var_reduc_pct": performance.variance_reduction_pct,
                "stability": performance.stability_score,
                "activation_freq": performance.activation_frequency,
                "activation_count": performance.activation_count,
                "rejection_impact": performance.rejection_impact,
                "rejection_rate": performance.rejection_rate,
                "bet_accept_rate": performance.bet_acceptance_rate,
                "calib_improvement": performance.calibration_improvement,
                "preds_affected": performance.predictions_affected,
                "decisions_changed": performance.decisions_changed,
                "regime": performance.regime
            })
            s.commit()
    except Exception as e:
        logger.warning(f"Failed to save layer performance: {e}")


def get_layer_timeseries(
    layer_name: str,
    run_limit: int = 20
) -> List[Dict]:
    """Get timeseries data for a specific layer."""
    
    from src.storage.db import get_session
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(run_limit)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return []
    
    try:
        with get_session() as s:
            results = s.execute(text("""
                SELECT 
                    l.run_id,
                    l.ev_contribution,
                    l.ev_contribution_pct,
                    l.stability_score,
                    l.activation_frequency,
                    l.rejection_rate,
                    l.regime,
                    e.start_timestamp
                FROM layer_performance_timeseries l
                JOIN experiment_runs e ON l.run_id = e.run_id
                WHERE l.run_id IN (:run_ids) AND l.layer_name = :layer_name
                ORDER BY e.start_timestamp DESC
                LIMIT :limit
            """), {"run_ids": ','.join(run_ids), "layer_name": layer_name, "limit": run_limit}).fetchall()
            
            return [
                {
                    "run_id": r.run_id,
                    "ev_contribution": r.ev_contribution,
                    "ev_contribution_pct": r.ev_contribution_pct,
                    "stability_score": r.stability_score,
                    "activation_frequency": r.activation_frequency,
                    "rejection_rate": r.rejection_rate,
                    "regime": r.regime,
                    "timestamp": r.start_timestamp
                }
                for r in results
            ]
    except Exception as e:
        logger.warning(f"Failed to get layer timeseries: {e}")
        return []


def get_layer_summary_ranked() -> Dict:
    """Get ranked layer summary by usefulness, stability, fragility, redundancy."""
    
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return {"error": "No runs available"}
    
    metrics = compute_layer_evolution_metrics(run_ids)
    
    by_utility = sorted(
        metrics["layers"].items(),
        key=lambda x: x[1]["utility_score"],
        reverse=True
    )
    
    by_stability = sorted(
        metrics["layers"].items(),
        key=lambda x: x[1]["stability_score"],
        reverse=True
    )
    
    by_fragility = sorted(
        metrics["layers"].items(),
        key=lambda x: x[1]["fragility_score"],
        reverse=True
    )
    
    by_redundancy = sorted(
        metrics["layers"].items(),
        key=lambda x: x[1]["redundancy_score"],
        reverse=True
    )
    
    return {
        "run_count": len(run_ids),
        "ranked_by_utility": [{"layer": l, "score": s["utility_score"]} for l, s in by_utility],
        "ranked_by_stability": [{"layer": l, "score": s["stability_score"]} for l, s in by_stability],
        "ranked_by_fragility": [{"layer": l, "score": s["fragility_score"]} for l, s in by_fragility],
        "ranked_by_redundancy": [{"layer": l, "score": s["redundancy_score"]} for l, s in by_redundancy],
        "best_layer": metrics.get("overall_trends", {}).get("best_layer"),
        "worst_layer": metrics.get("overall_trends", {}).get("worst_layer")
    }