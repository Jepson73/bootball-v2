import logging
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

LAYER_NAMES = ['model', 'calibration', 'league', 'latent', 'drift', 'risk']
LAYER_DISPLAY_NAMES = {
    'model': 'Model Layer',
    'calibration': 'Calibration Layer',
    'league': 'League Normalization',
    'latent': 'Latent State',
    'drift': 'Drift Adaptation',
    'risk': 'Risk Filtering'
}


@dataclass
class LayerMetrics:
    layer_name: str
    ev_contribution: float
    roi_contribution: float
    stability_score: float
    fragility_score: float
    redundancy_index: float
    failure_correlation: float
    convergence_score: float


@dataclass
class AblationResult:
    layer_removed: str
    baseline_ev: float
    ablated_ev: float
    ev_delta: float
    baseline_calibration: float
    ablated_calibration: float
    calibration_delta: float
    baseline_risk: float
    ablated_risk: float
    risk_delta: float
    prediction_count: int
    recommendation: str


class GovernanceEngine:
    """
    System Governance & Structural Optimization Layer.
    
    Responsibilities:
    - Layer utility persistence tracking
    - Layer promotion/demotion system
    - Structural pruning engine
    - Counterfactual layer ablation simulation
    """

    def __init__(
        self,
        promotion_threshold: float = 0.02,
        demotion_threshold: float = -0.02,
        fragility_threshold: float = 0.5,
        redundancy_threshold: float = 0.8,
        convergence_window: int = 10
    ):
        self.promotion_threshold = promotion_threshold
        self.demotion_threshold = demotion_threshold
        self.fragility_threshold = fragility_threshold
        self.redundancy_threshold = redundancy_threshold
        self.convergence_window = convergence_window

    def record_layer_governance_metrics(
        self,
        run_id: str,
        layer_metrics: List[LayerMetrics]
    ) -> None:
        """Record governance metrics for layers from a run."""
        from src.storage.db import get_session

        with get_session() as sess:
            from src.storage.models import LayerGovernanceMetrics

            for lm in layer_metrics:
                existing = sess.query(LayerGovernanceMetrics).filter(
                    LayerGovernanceMetrics.layer_name == lm.layer_name,
                    LayerGovernanceMetrics.run_id == run_id
                ).first()

                if existing:
                    existing.ev_contribution = lm.ev_contribution
                    existing.roi_contribution = lm.roi_contribution
                    existing.stability_score = lm.stability_score
                    existing.fragility_score = lm.fragility_score
                    existing.redundancy_index = lm.redundancy_index
                    existing.failure_correlation = lm.failure_correlation
                    existing.convergence_score = lm.convergence_score
                else:
                    record = LayerGovernanceMetrics(
                        layer_name=lm.layer_name,
                        run_id=run_id,
                        ev_contribution=lm.ev_contribution,
                        roi_contribution=lm.roi_contribution,
                        stability_score=lm.stability_score,
                        fragility_score=lm.fragility_score,
                        redundancy_index=lm.redundancy_index,
                        failure_correlation=lm.failure_correlation,
                        convergence_score=lm.convergence_score
                    )
                    sess.add(record)

            sess.commit()
            logger.info(f"Recorded governance metrics for {len(layer_metrics)} layers")

    def compute_layer_metrics_from_attribution(
        self,
        run_id: str,
        settled_only: bool = True
    ) -> List[LayerMetrics]:
        """Compute governance metrics from attribution data."""
        from src.storage.db import get_session

        layer_ev = {layer: 0.0 for layer in LAYER_NAMES}
        layer_count = {layer: 0 for layer in LAYER_NAMES}
        layer_wins = {layer: 0 for layer in LAYER_NAMES}
        layer_losses = {layer: 0 for layer in LAYER_NAMES}

        with get_session() as sess:
            from src.storage.models import PredictionAttribution

            query = sess.query(PredictionAttribution).filter(
                PredictionAttribution.run_id == run_id
            )

            if settled_only:
                query = query.filter(PredictionAttribution.settled == 1)

            rows = query.all()

            for row in rows:
                won = row.won if row.won is not None else 0

                contributions = {
                    'model': row.model_ev_contribution or 0.0,
                    'calibration': row.calibration_ev_contribution or 0.0,
                    'league': row.league_ev_contribution or 0.0,
                    'latent': row.latent_ev_contribution or 0.0,
                    'drift': row.drift_ev_contribution or 0.0,
                    'risk': row.risk_ev_contribution or 0.0
                }

                for layer, ev in contributions.items():
                    layer_ev[layer] += ev
                    layer_count[layer] += 1
                    if won:
                        layer_wins[layer] += 1
                    elif won == 0 and row.settled:
                        layer_losses[layer] += 1

        metrics = []

        for layer in LAYER_NAMES:
            count = layer_count[layer]
            if count == 0:
                continue

            avg_ev = layer_ev[layer] / count
            total_outcomes = layer_wins[layer] + layer_losses[layer]
            win_rate = layer_wins[layer] / total_outcomes if total_outcomes > 0 else 0.5

            ev_contribution = avg_ev
            roi_contribution = avg_ev * 100

            stability_score = min(1.0, win_rate)
            fragility_score = 1.0 - stability_score

            metrics.append(LayerMetrics(
                layer_name=layer,
                ev_contribution=round(ev_contribution, 6),
                roi_contribution=round(roi_contribution, 4),
                stability_score=round(stability_score, 4),
                fragility_score=round(fragility_score, 4),
                redundancy_index=0.0,
                failure_correlation=0.0,
                convergence_score=round(stability_score * abs(ev_contribution), 4)
            ))

        return metrics

    def compute_redundancy_index(self, run_id: str) -> Dict[str, float]:
        """Compute redundancy between layers based on correlation of decisions."""
        from src.storage.db import get_session

        layer_decisions = {layer: [] for layer in LAYER_NAMES}

        with get_session() as sess:
            from src.storage.models import PredictionAttribution

            rows = sess.query(PredictionAttribution).filter(
                PredictionAttribution.run_id == run_id,
                PredictionAttribution.settled == 1
            ).all()

            decision_fields = {
                'model': 'model_decision',
                'calibration': 'calibration_decision',
                'league': 'league_decision',
                'latent': 'latent_decision',
                'drift': 'drift_decision',
                'risk': 'risk_decision'
            }

            for row in rows:
                for layer, field in decision_fields.items():
                    decision = getattr(row, field, None)
                    if decision:
                        layer_decisions[layer].append(1 if decision == 'bet' else 0)

        redundancy = {layer: 0.0 for layer in LAYER_NAMES}

        layer_keys = list(layer_decisions.keys())
        for i, layer1 in enumerate(layer_keys):
            for layer2 in layer_keys[i+1:]:
                d1 = layer_decisions[layer1]
                d2 = layer_decisions[layer2]

                if len(d1) < 10 or len(d2) < 10:
                    continue

                correlation = self._compute_correlation(d1, d2)

                if correlation > self.redundancy_threshold:
                    redundancy[layer1] = max(redundancy[layer1], correlation)
                    redundancy[layer2] = max(redundancy[layer2], correlation)

        return redundancy

    def _compute_correlation(self, x: List[float], y: List[float]) -> float:
        """Compute Pearson correlation coefficient."""
        if len(x) != len(y) or len(x) < 2:
            return 0.0

        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        sum_y2 = sum(yi * yi for yi in y)

        numerator = n * sum_xy - sum_x * sum_y
        denominator = ((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y)) ** 0.5

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def evaluate_promotion_demotion(self, run_id: str) -> Dict[str, Any]:
        """Evaluate which layers should be promoted or demoted."""
        metrics = self.compute_layer_metrics_from_attribution(run_id)
        redundancy = self.compute_redundancy_index(run_id)

        recommendations = {}

        for lm in metrics:
            promotion_score = 0.0
            demotion_score = 0.0

            if lm.ev_contribution > self.promotion_threshold:
                promotion_score += 1.0

            if lm.stability_score > 0.6:
                promotion_score += 0.5

            if lm.fragility_score > self.fragility_threshold:
                demotion_score += 1.0

            redundancy_score = redundancy.get(lm.layer_name, 0.0)
            if redundancy_score > self.redundancy_threshold:
                demotion_score += redundancy_score

            if lm.ev_contribution < self.demotion_threshold:
                demotion_score += 1.0

            recommendations[lm.layer_name] = {
                'promotion_score': round(promotion_score, 2),
                'demotion_score': round(demotion_score, 2),
                'should_promote': promotion_score >= 1.5 and demotion_score < 1.0,
                'should_demote': demotion_score >= 1.5,
                'ev_contribution': lm.ev_contribution,
                'stability_score': lm.stability_score,
                'redundancy_index': redundancy_score
            }

        return recommendations

    def record_ablation_result(self, result: AblationResult) -> None:
        """Record layer ablation simulation result."""
        from src.storage.db import get_session

        with get_session() as sess:
            from src.storage.models import LayerAblationResults

            existing = sess.query(LayerAblationResults).filter(
                LayerAblationResults.run_id == result.layer_removed,
                LayerAblationResults.layer_removed == result.layer_removed
            ).first()

            if existing:
                existing.baseline_ev = result.baseline_ev
                existing.ablated_ev = result.ablated_ev
                existing.ev_delta = result.ev_delta
                existing.baseline_calibration = result.baseline_calibration
                existing.ablated_calibration = result.ablated_calibration
                existing.calibration_delta = result.calibration_delta
                existing.baseline_risk = result.baseline_risk
                existing.ablated_risk = result.ablated_risk
                existing.risk_delta = result.risk_delta
                existing.prediction_count = result.prediction_count
                existing.recommendation = result.recommendation
            else:
                record = LayerAblationResults(
                    run_id=result.layer_removed,
                    layer_removed=result.layer_removed,
                    baseline_ev=result.baseline_ev,
                    ablated_ev=result.ablated_ev,
                    ev_delta=result.ev_delta,
                    baseline_calibration=result.baseline_calibration,
                    ablated_calibration=result.ablated_calibration,
                    calibration_delta=result.calibration_delta,
                    baseline_risk=result.baseline_risk,
                    ablated_risk=result.ablated_risk,
                    risk_delta=result.risk_delta,
                    prediction_count=result.prediction_count,
                    recommendation=result.recommendation
                )
                sess.add(record)

            sess.commit()

    def simulate_layer_ablation(
        self,
        run_id: str,
        layer_to_remove: str
    ) -> AblationResult:
        """Simulate system performance without a specific layer."""
        from src.storage.db import get_session

        baseline_ev = 0.0
        baseline_calibration = 0.0
        baseline_risk = 0.0
        prediction_count = 0

        ablated_ev = 0.0

        with get_session() as sess:
            from src.storage.models import PredictionAttribution

            rows = sess.query(PredictionAttribution).filter(
                PredictionAttribution.run_id == run_id,
                PredictionAttribution.settled == 1
            ).all()

            prediction_count = len(rows)

            for row in rows:
                baseline_ev += (
                    (row.model_ev_contribution or 0.0) +
                    (row.calibration_ev_contribution or 0.0) +
                    (row.league_ev_contribution or 0.0) +
                    (row.latent_ev_contribution or 0.0) +
                    (row.drift_ev_contribution or 0.0) +
                    (row.risk_ev_contribution or 0.0)
                )

                if row.won:
                    baseline_calibration += 1.0

                if row.risk_filtered:
                    baseline_risk += 1.0

            if prediction_count > 0:
                baseline_ev /= prediction_count
                baseline_calibration /= prediction_count
                baseline_risk /= prediction_count

            for row in rows:
                ev_without_layer = 0.0

                if layer_to_remove == 'model':
                    pass
                elif layer_to_remove == 'calibration':
                    ev_without_layer = (row.model_ev_contribution or 0.0)
                elif layer_to_remove == 'league':
                    ev_without_layer = (
                        (row.model_ev_contribution or 0.0) +
                        (row.calibration_ev_contribution or 0.0)
                    )
                elif layer_to_remove == 'latent':
                    ev_without_layer = (
                        (row.model_ev_contribution or 0.0) +
                        (row.calibration_ev_contribution or 0.0) +
                        (row.league_ev_contribution or 0.0)
                    )
                elif layer_to_remove == 'drift':
                    ev_without_layer = (
                        (row.model_ev_contribution or 0.0) +
                        (row.calibration_ev_contribution or 0.0) +
                        (row.league_ev_contribution or 0.0) +
                        (row.latent_ev_contribution or 0.0)
                    )
                elif layer_to_remove == 'risk':
                    ev_without_layer = (
                        (row.model_ev_contribution or 0.0) +
                        (row.calibration_ev_contribution or 0.0) +
                        (row.league_ev_contribution or 0.0) +
                        (row.latent_ev_contribution or 0.0) +
                        (row.drift_ev_contribution or 0.0)
                    )

                ablated_ev += ev_without_layer

            if prediction_count > 0:
                ablated_ev /= prediction_count

        ev_delta = ablated_ev - baseline_ev

        if layer_to_remove == 'risk':
            ablated_risk = 0.0
        else:
            ablated_risk = baseline_risk

        risk_delta = ablated_risk - baseline_risk
        calibration_delta = 0.0
        ablated_calibration = baseline_calibration

        if ev_delta > 0.01:
            recommendation = 'remove'
        elif ev_delta < -0.02:
            recommendation = 'keep'
        else:
            recommendation = 'neutral'

        result = AblationResult(
            layer_removed=layer_to_remove,
            baseline_ev=round(baseline_ev, 6),
            ablated_ev=round(ablated_ev, 6),
            ev_delta=round(ev_delta, 6),
            baseline_calibration=round(baseline_calibration, 4),
            ablated_calibration=round(ablated_calibration, 4),
            calibration_delta=round(calibration_delta, 4),
            baseline_risk=round(baseline_risk, 4),
            ablated_risk=round(ablated_risk, 4),
            risk_delta=round(risk_delta, 4),
            prediction_count=prediction_count,
            recommendation=recommendation
        )

        self.record_ablation_result(result)
        return result

    def run_full_ablation_analysis(self, run_id: str) -> Dict[str, AblationResult]:
        """Run ablation analysis for all layers."""
        results = {}

        for layer in LAYER_NAMES:
            result = self.simulate_layer_ablation(run_id, layer)
            results[layer] = result

        return results

    def evaluate_architecture_recommendation(self, run_id: str) -> Dict[str, Any]:
        """Evaluate and recommend architecture changes."""
        ablation_results = self.run_full_ablation_analysis(run_id)
        recommendations = self.evaluate_promotion_demotion(run_id)

        layers_to_remove = []
        layers_to_reweight = {}
        layers_to_merge = []

        for layer, result in ablation_results.items():
            if result.recommendation == 'remove':
                layers_to_remove.append(layer)

            rec = recommendations.get(layer, {})
            if rec.get('ev_contribution', 0) > 0.05:
                layers_to_reweight[layer] = 1.1
            elif rec.get('ev_contribution', 0) < 0:
                layers_to_reweight[layer] = 0.9

        for layer in list(layers_to_remove):
            redundancy = recommendations.get(layer, {}).get('redundancy_index', 0)
            if redundancy > self.redundancy_threshold:
                layers_to_merge.append(layer)
                layers_to_remove.remove(layer)

        expected_ev_change = sum(
            ablation_results[layer].ev_delta
            for layer in layers_to_remove
        )

        return {
            'layers_to_remove': layers_to_remove,
            'layers_to_reweight': layers_to_reweight,
            'layers_to_merge': layers_to_merge,
            'recommended_architecture': f"optimized_v{run_id[:8]}",
            'expected_ev_change': round(expected_ev_change, 4),
            'ablation_results': {
                layer: {
                    'ev_delta': r.ev_delta,
                    'recommendation': r.recommendation
                }
                for layer, r in ablation_results.items()
            }
        }

    def get_layer_governance_summary(self) -> Dict[str, Any]:
        """Get summary of layer governance across recent runs."""
        from src.storage.db import get_session
        from sqlalchemy import text

        with get_session() as sess:
            from src.storage.models import LayerGovernanceMetrics

            result = sess.execute(text(
                "SELECT run_id FROM experiment_runs ORDER BY start_timestamp DESC LIMIT 10"
            ))
            run_ids = [row[0] for row in result.fetchall()]

            if not run_ids:
                return {'layers': {}, 'recommendations': {}}

            placeholders = ','.join([f":run{i}" for i in range(len(run_ids))])
            query = text(f"""
                SELECT layer_name,
                       AVG(ev_contribution) as avg_ev,
                       AVG(stability_score) as avg_stability,
                       AVG(fragility_score) as avg_fragility,
                       AVG(redundancy_index) as avg_redundancy,
                       COUNT(*) as count
                FROM layer_governance_metrics
                WHERE run_id IN ({placeholders})
                GROUP BY layer_name
            """)
            
            params = {f"run{i}": run_id for i, run_id in enumerate(run_ids)}
            result = sess.execute(query, params)
            
            layers_summary = {}
            for row in result.fetchall():
                layers_summary[row[0]] = {
                    'avg_ev_contribution': round(row[1] or 0, 4),
                    'avg_stability': round(row[2] or 0, 4),
                    'avg_fragility': round(row[3] or 0, 4),
                    'avg_redundancy': round(row[4] or 0, 4),
                    'sample_count': row[5]
                }

            return {
                'layers': layers_summary,
                'recent_run_count': len(run_ids),
                'recommendations': {}
            }


def get_governance_engine() -> GovernanceEngine:
    """Get singleton governance engine instance."""
    return GovernanceEngine()