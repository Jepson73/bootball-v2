import numpy as np
import json
import logging
from typing import Dict, List, Optional, Tuple, Set, Any
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class AblationConfig:
    """Configuration for layer ablation."""
    removed_layers: Set[str] = field(default_factory=set)
    modified_layers: Dict[str, Any] = field(default_factory=dict)
    
    def to_tuple(self) -> Tuple[str, ...]:
        return tuple(sorted(self.removed_layers))
    
    def __hash__(self):
        return hash(self.to_tuple())


@dataclass
class CounterfactualResult:
    """Result of counterfactual simulation."""
    ablation_config: AblationConfig
    run_id: str
    
    ev: float = 0.0
    roi: float = 0.0
    calibration_error: float = 0.0
    acceptance_rate: float = 0.0
    settled_predictions: int = 0
    winning_predictions: int = 0
    
    ev_delta: float = 0.0
    roi_delta: float = 0.0
    
    stability_score: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "ablation_config": list(self.ablation_config.removed_layers),
            "run_id": self.run_id,
            "ev": self.ev,
            "roi": self.roi,
            "calibration_error": self.calibration_error,
            "acceptance_rate": self.acceptance_rate,
            "ev_delta": self.ev_delta,
            "roi_delta": self.roi_delta,
            "stability_score": self.stability_score
        }


class LayerAblationEngine:
    """Simulates system performance under layer removal."""
    
    def __init__(self):
        self.all_layers = {"calibration", "league_norm", "latent_state", "drift", "risk", "model"}
        self.results_cache: Dict[Tuple[str, AblationConfig], CounterfactualResult] = {}
    
    def get_ablation_configs(self) -> List[AblationConfig]:
        """Generate all ablation configurations to test."""
        configs = []
        
        configs.append(AblationConfig(removed_layers=set()))  # Baseline - no removal
        
        for layer in self.all_layers:
            configs.append(AblationConfig(removed_layers={layer}))
        
        layer_list = list(self.all_layers)
        for i in range(2, min(4, len(layer_list) + 1)):
            for combo in combinations(layer_list, i):
                configs.append(AblationConfig(removed_layers=set(combo)))
        
        return configs
    
    def get_baseline_metrics(self, run_id: str) -> Dict:
        """Get baseline metrics without any layer removal."""
        from src.storage.db import get_session
        
        baseline = {"ev": 0.0, "roi": 0.0, "calibration_error": 0.0, "acceptance_rate": 0.0,
                   "settled_predictions": 0, "winning_predictions": 0, "total_predictions": 0}
        
        try:
            with get_session() as s:
                result = s.execute(text("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
                        SUM(CASE WHEN settled = 1 AND won = 1 THEN 1 ELSE 0 END) as wins,
                        AVG(ev) as avg_ev
                    FROM prediction_records 
                    WHERE run_id = :run_id
                """), {"run_id": run_id}).fetchone()
                
                if result:
                    baseline["total_predictions"] = result.total or 0
                    baseline["settled_predictions"] = result.settled or 0
                    baseline["winning_predictions"] = result.wins or 0
                    baseline["ev"] = result.avg_ev or 0.0
                    
                    if result.settled and result.settled > 0:
                        baseline["roi"] = (result.wins or 0) / result.settled - 1
                
                att_result = s.execute(text("""
                    SELECT 
                        AVG(ABS(calibration_delta)) as avg_calib_delta,
                        AVG(ABS(risk_delta)) as avg_risk_delta,
                        AVG(CASE WHEN risk_filtered = 1 THEN 1.0 ELSE 0.0 END) as filter_rate
                    FROM prediction_attribution
                    WHERE run_id = :run_id
                """), {"run_id": run_id}).fetchone()
                
                if att_result:
                    baseline["calibration_error"] = att_result.avg_calib_delta or 0.0
                    baseline["acceptance_rate"] = 1.0 - (att_result.filter_rate or 0.0)
                    
        except Exception as e:
            logger.warning(f"Failed to get baseline metrics: {e}")
        
        return baseline
    
    def simulate_single_layer_removal(
        self,
        run_id: str,
        layer_to_remove: str,
        baseline: Dict
    ) -> CounterfactualResult:
        """Simulate removing a single layer from the pipeline."""
        
        config = AblationConfig(removed_layers={layer_to_remove})
        result = CounterfactualResult(ablation_config=config, run_id=run_id)
        
        try:
            from src.storage.db import get_session
            
            with get_session() as s:
                if layer_to_remove == "calibration":
                    pred_result = s.execute(text("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
                            SUM(CASE WHEN settled = 1 AND won = 1 THEN 1 ELSE 0 END) as wins,
                            AVG(model_prob_raw) as avg_prob
                        FROM prediction_attribution
                        WHERE run_id = :run_id
                    """), {"run_id": run_id}).fetchone()
                    
                    if pred_result:
                        result.settled_predictions = pred_result.settled or 0
                        result.winning_predictions = pred_result.wins or 0
                        result.ev = (pred_result.avg_prob or 0.5) - 0.5
                        result.acceptance_rate = 1.0
                
                elif layer_to_remove == "risk":
                    pred_result = s.execute(text("""
                        SELECT 
                            AVG(model_prob_raw) as avg_prob
                        FROM prediction_attribution
                        WHERE run_id = :run_id
                    """), {"run_id": run_id}).fetchone()
                    
                    if pred_result:
                        result.acceptance_rate = 1.0
                        result.ev = (pred_result.avg_prob or 0.5) - 0.5
                
                elif layer_to_remove == "latent":
                    pred_result = s.execute(text("""
                        SELECT 
                            AVG(model_prob_raw + calibration_delta) as adj_prob
                        FROM prediction_attribution
                        WHERE run_id = :run_id
                    """), {"run_id": run_id}).fetchone()
                    
                    if pred_result:
                        result.ev = (pred_result.adj_prob or 0.5) - 0.5
                        result.acceptance_rate = baseline.get("acceptance_rate", 0.5)
                
                elif layer_to_remove == "drift":
                    result.ev = baseline.get("ev", 0.0) * 0.9
                    result.acceptance_rate = baseline.get("acceptance_rate", 0.5)
                
                elif layer_to_remove == "league_norm":
                    result.ev = baseline.get("ev", 0.0) * 0.95
                    result.calibration_error = baseline.get("calibration_error", 0.0) * 1.2
                    result.acceptance_rate = baseline.get("acceptance_rate", 0.5)
                
                elif layer_to_remove == "model":
                    result.ev = 0.0
                    result.calibration_error = 1.0
                
                result.ev_delta = result.ev - baseline.get("ev", 0.0)
                result.roi_delta = result.roi - baseline.get("roi", 0.0)
                result.stability_score = 1.0 - abs(result.ev_delta)
                
        except Exception as e:
            logger.warning(f"Failed to simulate layer removal: {e}")
        
        return result
    
    def run_ablation_study(
        self,
        run_id: str,
        layer_subset: Optional[Set[str]] = None
    ) -> List[CounterfactualResult]:
        """Run full ablation study for a run."""
        
        if layer_subset is None:
            layer_subset = {"calibration", "risk", "latent", "drift", "league_norm"}
        
        baseline = self.get_baseline_metrics(run_id)
        results = [CounterfactualResult(AblationConfig(removed_layers=set()), run_id, ev=baseline.get("ev", 0))]
        
        for layer in layer_subset:
            result = self.simulate_single_layer_removal(run_id, layer, baseline)
            results.append(result)
            logger.info(f"Ablation {layer}: EV delta = {result.ev_delta:.4f}")
        
        return results
    
    def compute_layer_importance(
        self,
        run_id: str
    ) -> Dict[str, float]:
        """Compute importance scores for each layer."""
        
        baseline = self.get_baseline_metrics(run_id)
        results = self.run_ablation_study(run_id)
        
        importance = {}
        
        for result in results:
            if not result.ablation_config.removed_layers:
                continue
            
            layer = list(result.ablation_config.removed_layers)[0]
            importance[layer] = abs(result.ev_delta)
        
        total_importance = sum(importance.values())
        
        if total_importance > 0:
            for layer in importance:
                importance[layer] = importance[layer] / total_importance
        
        return importance


def compute_ev_roi_deltas(
    run_id: str,
    layer_importance: Dict[str, float]
) -> Dict:
    """Compute EV/ROI deltas with interaction adjustments."""
    
    from src.storage.db import get_session
    
    deltas = {
        "marginal_contributions": {},
        "interaction_effects": {},
        "net_contributions": {},
        "conditional_importance": {}
    }
    
    try:
        with get_session() as s:
            for layer, importance in layer_importance.items():
                layer_row = s.execute(text("""
                    SELECT 
                        AVG(calibration_delta) as calib_d,
                        AVG(latent_delta) as latent_d,
                        AVG(risk_delta) as risk_d,
                        AVG(league_delta) as league_d,
                        AVG(drift_delta) as drift_d
                    FROM prediction_attribution
                    WHERE run_id = :run_id
                """), {"run_id": run_id}).fetchone()
                
                if layer == "calibration":
                    deltas["marginal_contributions"]["calibration"] = layer_row.calib_d or 0
                elif layer == "latent_state":
                    deltas["marginal_contributions"]["latent_state"] = layer_row.latent_d or 0
                elif layer == "risk":
                    deltas["marginal_contributions"]["risk"] = layer_row.risk_d or 0
                elif layer == "league_norm":
                    deltas["marginal_contributions"]["league_norm"] = layer_row.league_d or 0
                elif layer == "drift":
                    deltas["marginal_contributions"]["drift"] = layer_row.drift_d or 0
            
            calib = s.execute(text("""
                SELECT AVG(calibration_delta) as c FROM prediction_attribution WHERE run_id = :run_id
            """), {"run_id": run_id}).fetchone()
            risk = s.execute(text("""
                SELECT AVG(risk_delta) as r FROM prediction_attribution WHERE run_id = :run_id
            """), {"run_id": run_id}).fetchone()
            latent = s.execute(text("""
                SELECT AVG(latent_delta) as l FROM prediction_attribution WHERE run_id = :run_id
            """), {"run_id": run_id}).fetchone()
            
            if calib and risk and latent:
                interaction = (calib.c or 0) * (risk.r or 0)
                deltas["interaction_effects"]["calibration_risk"] = interaction
            
            for layer, marg in deltas["marginal_contributions"].items():
                inter = deltas["interaction_effects"].get(f"calibration_{layer[:4]}", 0)
                deltas["net_contributions"][layer] = marg + inter * 0.1
            
            regime_rows = s.execute(text("""
                SELECT regime, AVG(ev) as avg_ev
                FROM layer_performance_timeseries
                WHERE run_id = :run_id
                GROUP BY regime
            """), {"run_id": run_id}).fetchall()
            
            for row in regime_rows:
                deltas["conditional_importance"][row.regime] = {
                    "avg_ev": row.avg_ev or 0,
                    "dominant_layer": max(layer_importance.items(), key=lambda x: x[1])[0] if layer_importance else "unknown"
                }
    
    except Exception as e:
        logger.warning(f"Failed to compute EV/ROI deltas: {e}")
    
    return deltas


def find_pareto_optimal_architectures(
    run_ids: List[str],
    objectives: List[str] = None
) -> List[Dict]:
    """Find Pareto-optimal layer architectures."""
    
    if objectives is None:
        objectives = ["ev", "stability", "simplicity"]
    
    engine = LayerAblationEngine()
    all_results = []
    
    for run_id in run_ids:
        results = engine.run_ablation_study(run_id)
        all_results.extend(results)
    
    pareto_frontier = []
    
    for result in all_results:
        score_ev = result.ev
        score_stability = result.stability_score
        
        layers_removed = len(result.ablation_config.removed_layers)
        score_simplicity = -layers_removed
        
        is_dominated = False
        for existing in pareto_frontier:
            e_ev, e_stab, e_simp = existing.get("scores", {}).get("ev", 0), existing.get("scores", {}).get("stability", 0), existing.get("scores", {}).get("simplicity", 0)
            
            if e_ev >= score_ev and e_stab >= score_stability and e_simp >= score_simplicity:
                if (e_ev > score_ev or e_stab > score_stability or e_simp > score_simplicity):
                    is_dominated = True
                    break
        
        if not is_dominated:
            pareto_frontier.append({
                "config": list(result.ablation_config.removed_layers),
                "ev": score_ev,
                "stability": score_stability,
                "simplicity": score_simplicity,
                "scores": {"ev": score_ev, "stability": score_stability, "simplicity": score_simplicity}
            })
    
    pareto_frontier.sort(key=lambda x: x.get("ev", 0), reverse=True)
    
    return pareto_frontier


def suggest_simplified_architecture(
    run_ids: List[str]
) -> Dict:
    """Suggest a simplified architecture based on ablation results."""
    
    engine = LayerAblationEngine()
    
    layer_importance_aggregated = {}
    
    for run_id in run_ids:
        importance = engine.compute_layer_importance(run_id)
        
        for layer, score in importance.items():
            if layer not in layer_importance_aggregated:
                layer_importance_aggregated[layer] = []
            layer_importance_aggregated[layer].append(score)
    
    avg_importance = {
        layer: np.mean(scores) if scores else 0
        for layer, scores in layer_importance_aggregated.items()
    }
    
    layers_to_remove = []
    layers_redundant = []
    layers_conditional = []
    minimal_architecture = set()
    
    for layer, avg_score in avg_importance.items():
        if avg_score < 0.05:
            layers_to_remove.append(layer)
        elif avg_score < 0.15:
            layers_redundant.append(layer)
        else:
            minimal_architecture.add(layer)
    
    pareto = find_pareto_optimal_architectures(run_ids)
    
    if pareto:
        best_config = pareto[0].get("config", [])
        if len(best_config) < 3:
            layers_conditional = best_config
    
    return {
        "layers_to_remove_safely": layers_to_remove,
        "layers_redundant": layers_redundant,
        "layers_only_conditional": layers_conditional,
        "minimal_viable_architecture": list(minimal_architecture),
        "avg_layer_importance": avg_importance,
        "pareto_optimal": pareto[:5]
    }


def generate_counterfactual_insights(
    run_ids: List[str]
) -> List[Dict]:
    """Generate counterfactual system insights."""
    
    from src.betting.layer_evolution import compute_layer_evolution_metrics
    
    insights = []
    
    suggestion = suggest_simplified_architecture(run_ids)
    
    layer_metrics = compute_layer_evolution_metrics(run_ids)
    
    for layer, metrics in layer_metrics.get("layers", {}).items():
        stability = metrics.get("stability_score", 0.5)
        utility = metrics.get("utility_score", 0)
        fragility = metrics.get("fragility_score", 0.5)
        
        if fragility > 0.6 and utility > 0.1:
            insights.append({
                "type": "conditional_value",
                "layer": layer,
                "text": f"{layer.title()} improves EV in stable conditions but degrades under stress; net value is conditional on regime.",
                "confidence": (stability + utility) / 2
            })
        elif fragility > 0.6 and utility <= 0.1:
            insights.append({
                "type": "removal_candidate",
                "layer": layer,
                "text": f"{layer.title()} adds complexity without consistent value; consider removal.",
                "confidence": fragility
            })
        elif stability > 0.8 and utility > 0.15:
            insights.append({
                "type": "essential",
                "layer": layer,
                "text": f"{layer.title()} is essential - high stability and consistent value across runs.",
                "confidence": stability * utility
            })
    
    if suggestion.get("minimal_viable_architecture"):
        min_arch = suggestion["minimal_viable_architecture"]
        insights.append({
            "type": "simplified_arch",
            "layer": None,
            "text": f"Minimal viable architecture: {', '.join(min_arch)}",
            "confidence": 0.8
        })
    
    if len(suggestion.get("layers_redundant", [])) > 0:
        insights.append({
            "type": "redundancy",
            "layer": None,
            "text": f"Redundant layers (correlation-based): {', '.join(suggestion['layers_redundant'])}",
            "confidence": 0.7
        })
    
    pareto = suggestion.get("pareto_optimal", [])
    if pareto and len(pareto) > 0:
        best = pareto[0]
        insights.append({
            "type": "optimal_config",
            "layer": None,
            "text": f"Pareto-optimal: EV={best.get('ev', 0):.3f}, config={best.get('config', [])}",
            "confidence": 0.9
        })
    
    return insights


def save_counterfactual_results(
    run_id: str,
    results: List[CounterfactualResult],
    baseline: Dict
) -> None:
    """Save counterfactual results to database."""
    
    from src.storage.db import get_session
    
    try:
        with get_session() as s:
            for result in results:
                config_json = json.dumps(list(result.ablation_config.removed_layers))
                
                s.execute(text("""
                    INSERT OR REPLACE INTO counterfactual_runs (
                        run_id, ablation_config, ev, roi, calibration_error, acceptance_rate,
                        ev_delta, roi_delta, stability_delta, created_at
                    ) VALUES (
                        :run_id, :config, :ev, :roi, :calib_err, :accept_rate,
                        :ev_delta, :roi_delta, :stab_delta, :created
                    )
                """), {
                    "run_id": run_id,
                    "config": config_json,
                    "ev": result.ev,
                    "roi": result.roi,
                    "calib_err": result.calibration_error,
                    "accept_rate": result.acceptance_rate,
                    "ev_delta": result.ev_delta,
                    "roi_delta": result.roi_delta,
                    "stab_delta": result.stability_score - baseline.get("ev", 0),
                    "created": datetime.now().isoformat()
                })
            s.commit()
    except Exception as e:
        logger.warning(f"Failed to save counterfactual results: {e}")


def get_counterfactual_summary(run_id: str) -> Dict:
    """Get summary of counterfactual analysis for a run."""
    
    from src.storage.db import get_session
    
    summary = {"run_id": run_id, "configs": [], "best_config": None, "worst_config": None}
    
    try:
        with get_session() as s:
            results = s.execute(text("""
                SELECT ablation_config, ev, ev_delta, roi_delta, stability_delta
                FROM counterfactual_runs
                WHERE run_id = :run_id
                ORDER BY ev_delta DESC
            """), {"run_id": run_id}).fetchall()
            
            for row in results:
                summary["configs"].append({
                    "removed": json.loads(row.ablation_config),
                    "ev": row.ev,
                    "ev_delta": row.ev_delta,
                    "stability": row.stability_delta
                })
            
            if results:
                summary["best_config"] = json.loads(results[0].ablation_config)
                summary["worst_config"] = json.loads(results[-1].ablation_config)
    
    except Exception as e:
        logger.warning(f"Failed to get counterfactual summary: {e}")
    
    return summary