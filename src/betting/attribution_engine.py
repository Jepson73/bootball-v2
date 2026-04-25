import numpy as np
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class LayerContribution:
    """Stores contribution from each system layer."""
    
    layer_name: str
    input_prob: float
    output_prob: float
    delta_prob: float
    delta_ev: float
    decision: Optional[str] = None
    decision_changed: bool = False


@dataclass
class AttributionResult:
    """Complete attribution for a single prediction."""
    
    prediction_id: int
    fixture_id: int
    market: str
    run_id: str
    
    model_prob_raw: float
    
    calibration: LayerContribution = None
    league: LayerContribution = None
    latent: LayerContribution = None
    drift: LayerContribution = None
    risk: LayerContribution = None
    
    final_prob: float = 0.0
    final_ev: float = 0.0
    final_decision: str = ""
    
    actual_outcome: Optional[str] = None
    settled: bool = False
    won: Optional[bool] = None
    
    total_ev_contribution: float = 0.0
    layers_contributed_value: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "prediction_id": self.prediction_id,
            "fixture_id": self.fixture_id,
            "market": self.market,
            "run_id": self.run_id,
            "model_prob_raw": self.model_prob_raw,
            "calibration_delta": self.calibration.delta_prob if self.calibration else 0,
            "calibration_prob": self.calibration.output_prob if self.calibration else None,
            "league_delta": self.league.delta_prob if self.league else 0,
            "league_adjusted_prob": self.league.output_prob if self.league else None,
            "latent_delta": self.latent.delta_prob if self.latent else 0,
            "latent_adjusted_prob": self.latent.output_prob if self.latent else None,
            "drift_delta": self.drift.delta_prob if self.drift else 0,
            "drift_adjusted_prob": self.drift.output_prob if self.drift else None,
            "risk_delta": self.risk.delta_prob if self.risk else 0,
            "risk_filtered": self.risk.decision_changed if self.risk else False,
            "final_prob": self.final_prob,
            "model_ev_contribution": self.calibration.delta_ev if self.calibration else 0,
            "calibration_ev_contribution": self.calibration.delta_ev if self.calibration else 0,
            "league_ev_contribution": self.league.delta_ev if self.league else 0,
            "latent_ev_contribution": self.latent.delta_ev if self.latent else 0,
            "drift_ev_contribution": self.drift.delta_ev if self.drift else 0,
            "risk_ev_contribution": self.risk.delta_ev if self.risk else 0,
            "actual_outcome": self.actual_outcome,
            "settled": self.settled,
            "won": self.won,
        }


class AttributionEngine:
    """Computes layer-by-layer contribution to predictions."""
    
    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id
        self.attributions: List[AttributionResult] = []
    
    def compute_layer_contribution(
        self,
        layer_name: str,
        input_prob: float,
        output_prob: float,
        odds_decimal: float,
        baseline_outcome: Optional[str] = None
    ) -> LayerContribution:
        """Compute contribution for a single layer."""
        
        delta_prob = output_prob - input_prob
        
        input_ev = input_prob * odds_decimal - (1 - input_prob)
        output_ev = output_prob * odds_decimal - (1 - output_prob)
        delta_ev = output_ev - input_ev
        
        in_decision = self._decision_from_prob(input_prob)
        out_decision = self._decision_from_prob(output_prob)
        
        return LayerContribution(
            layer_name=layer_name,
            input_prob=input_prob,
            output_prob=output_prob,
            delta_prob=delta_prob,
            delta_ev=delta_ev,
            decision=out_decision,
            decision_changed=(in_decision != out_decision)
        )
    
    def _decision_from_prob(self, prob: float, threshold: float = 0.5) -> str:
        """Convert probability to decision."""
        if prob >= threshold:
            return "bet"
        return "no_bet"
    
    def compute_attribution(
        self,
        prediction_id: int,
        fixture_id: int,
        market: str,
        model_prob_raw: float,
        calibration_prob: float,
        league_adjusted_prob: float,
        latent_adjusted_prob: float,
        drift_adjusted_prob: float,
        final_prob: float,
        odds_decimal: float,
        risk_filtered: bool = False,
        actual_outcome: Optional[str] = None,
        settled: bool = False,
        won: Optional[bool] = None,
    ) -> AttributionResult:
        """Compute full attribution for a prediction."""
        
        result = AttributionResult(
            prediction_id=prediction_id,
            fixture_id=fixture_id,
            market=market,
            run_id=self.run_id or "",
            model_prob_raw=model_prob_raw,
            final_prob=final_prob,
            final_ev=final_prob * odds_decimal - (1 - final_prob),
            actual_outcome=actual_outcome,
            settled=settled,
            won=won,
        )
        
        result.calibration = self.compute_layer_contribution(
            "calibration",
            model_prob_raw,
            calibration_prob,
            odds_decimal
        )
        
        result.league = self.compute_layer_contribution(
            "league",
            calibration_prob,
            league_adjusted_prob,
            odds_decimal
        )
        
        result.latent = self.compute_layer_contribution(
            "latent",
            league_adjusted_prob,
            latent_adjusted_prob,
            odds_decimal
        )
        
        result.drift = self.compute_layer_contribution(
            "drift",
            latent_adjusted_prob,
            drift_adjusted_prob,
            odds_decimal
        )
        
        effective_prob = drift_adjusted_prob if not risk_filtered else 0.0
        
        result.risk = self.compute_layer_contribution(
            "risk",
            drift_adjusted_prob,
            effective_prob,
            odds_decimal
        )
        result.risk.decision_changed = risk_filtered
        
        result.total_ev_contribution = (
            result.calibration.delta_ev +
            result.league.delta_ev +
            result.latent.delta_ev +
            result.drift.delta_ev +
            result.risk.delta_ev
        )
        
        result.layers_contributed_value = abs(result.total_ev_contribution) > 0.01
        
        result.final_decision = "bet" if final_prob >= 0.5 else "no_bet"
        
        if result.calibration:
            result.calibration.decision = result.final_decision
        if result.league:
            result.league.decision = result.final_decision
        if result.latent:
            result.latent.decision = result.final_decision
        if result.drift:
            result.drift.decision = result.final_decision
        if result.risk:
            result.risk.decision = "filtered" if risk_filtered else result.final_decision
        
        self.attributions.append(result)
        return result
    
    def save_to_database(self, prediction_id: int):
        """Save attribution to database."""
        from src.storage.db import get_session
        
        if not self.attributions:
            return
        
        attr = self.attributions[-1]
        
        try:
            with get_session() as s:
                s.execute(text("""
                    INSERT OR REPLACE INTO prediction_attribution (
                        prediction_id, run_id, fixture_id, market,
                        model_prob_raw,
                        calibration_delta, calibration_prob,
                        league_delta, league_adjusted_prob,
                        latent_delta, latent_adjusted_prob,
                        drift_delta, drift_adjusted_prob,
                        risk_delta, risk_filtered, final_prob,
                        model_ev_contribution, calibration_ev_contribution,
                        league_ev_contribution, latent_ev_contribution,
                        drift_ev_contribution, risk_ev_contribution,
                        model_decision, calibration_decision, league_decision,
                        latent_decision, drift_decision, final_decision,
                        actual_outcome, settled, won
                    ) VALUES (
                        :pred_id, :run_id, :fix_id, :market,
                        :model_raw,
                        :calib_delta, :calib_prob,
                        :league_delta, :league_prob,
                        :latent_delta, :latent_prob,
                        :drift_delta, :drift_prob,
                        :risk_delta, :risk_filt, :final_prob,
                        :model_ev, :calib_ev, :league_ev, :latent_ev, :drift_ev, :risk_ev,
                        :model_dec, :calib_dec, :league_dec, :latent_dec, :drift_dec, :final_dec,
                        :actual, :settled, :won
                    )
                """), {
                    "pred_id": attr.prediction_id,
                    "run_id": attr.run_id,
                    "fix_id": attr.fixture_id,
                    "market": attr.market,
                    "model_raw": attr.model_prob_raw,
                    "calib_delta": attr.calibration.delta_prob if attr.calibration else 0,
                    "calib_prob": attr.calibration.output_prob if attr.calibration else None,
                    "league_delta": attr.league.delta_prob if attr.league else 0,
                    "league_prob": attr.league.output_prob if attr.league else None,
                    "latent_delta": attr.latent.delta_prob if attr.latent else 0,
                    "latent_prob": attr.latent.output_prob if attr.latent else None,
                    "drift_delta": attr.drift.delta_prob if attr.drift else 0,
                    "drift_prob": attr.drift.output_prob if attr.drift else None,
                    "risk_delta": attr.risk.delta_prob if attr.risk else 0,
                    "risk_filt": 1 if attr.risk and attr.risk.decision_changed else 0,
                    "final_prob": attr.final_prob,
                    "model_ev": 0,
                    "calib_ev": attr.calibration.delta_ev if attr.calibration else 0,
                    "league_ev": attr.league.delta_ev if attr.league else 0,
                    "latent_ev": attr.latent.delta_ev if attr.latent else 0,
                    "drift_ev": attr.drift.delta_ev if attr.drift else 0,
                    "risk_ev": attr.risk.delta_ev if attr.risk else 0,
                    "model_dec": "bet" if attr.model_prob_raw >= 0.5 else "no_bet",
                    "calib_dec": attr.calibration.decision if attr.calibration else None,
                    "league_dec": attr.league.decision if attr.league else None,
                    "latent_dec": attr.latent.decision if attr.latent else None,
                    "drift_dec": attr.drift.decision if attr.drift else None,
                    "final_dec": attr.final_decision,
                    "actual": attr.actual_outcome,
                    "settled": 1 if attr.settled else 0,
                    "won": 1 if attr.won else (0 if attr.won is not None else None),
                })
                s.commit()
        except Exception as e:
            logger.warning(f"Failed to save attribution: {e}")


class LayerSensitivityAnalyzer:
    """Analyzes which layers contribute most to system performance."""
    
    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id
    
    def compute_layer_sensitivity(self, market: Optional[str] = None) -> Dict:
        """Compute sensitivity metrics for each layer."""
        from src.storage.db import get_session
        
        query = """
            SELECT 
                market,
                COUNT(*) as total,
                AVG(calibration_delta) as calib_avg,
                AVG(league_delta) as league_avg,
                AVG(latent_delta) as latent_avg,
                AVG(drift_delta) as drift_avg,
                AVG(risk_delta) as risk_avg,
                AVG(calibration_ev_contribution) as calib_ev_avg,
                AVG(league_ev_contribution) as league_ev_avg,
                AVG(latent_ev_contribution) as latent_ev_avg,
                AVG(drift_ev_contribution) as drift_ev_avg,
                AVG(risk_ev_contribution) as risk_ev_avg,
                AVG(model_prob_raw) as model_avg,
                AVG(final_prob) as final_avg
            FROM prediction_attribution
            WHERE run_id = :run_id
        """
        
        params = {"run_id": self.run_id}
        if market:
            query += " AND market = :market"
            params["market"] = market
        
        query += " GROUP BY market"
        
        results = {}
        
        try:
            with get_session() as s:
                rows = s.execute(text(query), params).fetchall()
                
                for row in rows:
                    model_var = np.var([row.model_avg]) if row.total > 1 else 0
                    final_var = np.var([row.final_avg]) if row.total > 1 else 0
                    variance_reduction = (model_var - final_var) / max(model_var, 0.001)
                    
                    layer_impacts = {
                        "calibration": abs(row.calib_ev_avg or 0),
                        "league": abs(row.league_ev_avg or 0),
                        "latent": abs(row.latent_ev_avg or 0),
                        "drift": abs(row.drift_ev_avg or 0),
                        "risk": abs(row.risk_ev_avg or 0),
                    }
                    
                    most_impactful = max(layer_impacts, key=layer_impacts.get)
                    least_impactful = min(layer_impacts, key=layer_impacts.get)
                    
                    results[row.market] = {
                        "total_predictions": row.total,
                        "layer_deltas": {
                            "calibration": row.calib_avg or 0,
                            "league": row.league_avg or 0,
                            "latent": row.latent_avg or 0,
                            "drift": row.drift_avg or 0,
                            "risk": row.risk_avg or 0,
                        },
                        "ev_contributions": layer_impacts,
                        "most_impactful_layer": most_impactful,
                        "least_impactful_layer": least_impactful,
                        "variance_reduction_pct": variance_reduction * 100,
                    }
        except Exception as e:
            logger.warning(f"Failed to compute layer sensitivity: {e}")
        
        return results
    
    def get_layer_stability(self) -> Dict:
        """Compute stability metrics per layer."""
        from src.storage.db import get_session
        
        query = """
            SELECT 
                market,
                AVG(calibration_delta) as calib_avg,
                AVG(league_delta) as league_avg,
                AVG(latent_delta) as latent_avg,
                AVG(drift_delta) as drift_avg,
                AVG(risk_delta) as risk_avg,
                AVG(calibration_delta * calibration_delta) as calib_sq_avg,
                AVG(league_delta * league_delta) as league_sq_avg,
                AVG(latent_delta * latent_delta) as latent_sq_avg,
                AVG(drift_delta * drift_delta) as drift_sq_avg,
                AVG(risk_delta * risk_delta) as risk_sq_avg,
                COUNT(*) as total
            FROM prediction_attribution
            WHERE run_id = :run_id
            GROUP BY market
        """
        
        try:
            with get_session() as s:
                rows = s.execute(text(query), {"run_id": self.run_id}).fetchall()
                
                stability = {}
                for row in rows:
                    calib_var = max(0, (row.calib_sq_avg or 0) - (row.calib_avg or 0) ** 2)
                    league_var = max(0, (row.league_sq_avg or 0) - (row.league_avg or 0) ** 2)
                    latent_var = max(0, (row.latent_sq_avg or 0) - (row.latent_avg or 0) ** 2)
                    drift_var = max(0, (row.drift_sq_avg or 0) - (row.drift_avg or 0) ** 2)
                    risk_var = max(0, (row.risk_sq_avg or 0) - (row.risk_avg or 0) ** 2)
                    
                    stds = {
                        "calibration": calib_var ** 0.5,
                        "league": league_var ** 0.5,
                        "latent": latent_var ** 0.5,
                        "drift": drift_var ** 0.5,
                        "risk": risk_var ** 0.5,
                    }
                    
                    most_unstable = max(stds, key=stds.get)
                    most_stable = min(stds, key=stds.get)
                    
                    stability[row.market] = {
                        "layer_stdev": stds,
                        "most_unstable_layer": most_unstable,
                        "most_stable_layer": most_stable,
                    }
                return stability
        except Exception as e:
            logger.warning(f"Failed to compute stability: {e}")
            return {}


def compute_run_attribution_summary(run_id: str) -> Dict:
    """Compute attribution summary for a run."""
    from src.storage.db import get_session
    
    summary = {
        "run_id": run_id,
        "markets": {},
        "overall": {
            "total_predictions": 0,
            "total_ev_contribution": 0,
            "layers_contributed_value_count": 0,
        }
    }
    
    try:
        with get_session() as s:
            rows = s.execute(text("""
                SELECT 
                    market,
                    COUNT(*) as total,
                    SUM(calibration_ev_contribution + league_ev_contribution + 
                        latent_ev_contribution + drift_ev_contribution + risk_ev_contribution) as total_ev,
                    SUM(CASE WHEN (calibration_ev_contribution + league_ev_contribution + 
                                  latent_ev_contribution + drift_ev_contribution + risk_ev_contribution) != 0 
                             THEN 1 ELSE 0 END) as layers_contributed
                FROM prediction_attribution
                WHERE run_id = :run_id
                GROUP BY market
            """), {"run_id": run_id}).fetchall()
            
            for row in rows:
                summary["markets"][row.market] = {
                    "total_predictions": row.total,
                    "total_ev_contribution": row.total_ev or 0,
                    "layers_contributed_value_count": row.layers_contributed or 0,
                }
                
                summary["overall"]["total_predictions"] += row.total
                summary["overall"]["total_ev_contribution"] += (row.total_ev or 0)
                summary["overall"]["layers_contributed_value_count"] += (row.layers_contributed or 0)
            
            if summary["overall"]["total_predictions"] > 0:
                summary["overall"]["avg_ev_contribution_per_pred"] = (
                    summary["overall"]["total_ev_contribution"] / 
                    summary["overall"]["total_predictions"]
                )
            
    except Exception as e:
        logger.warning(f"Failed to compute run attribution summary: {e}")
    
    return summary


def get_layer_diagnostics(run_id: str) -> Dict:
    """Get comprehensive layer diagnostics for a run."""
    
    analyzer = LayerSensitivityAnalyzer(run_id)
    sensitivity = analyzer.compute_layer_sensitivity()
    stability = analyzer.get_layer_stability()
    summary = compute_run_attribution_summary(run_id)
    
    return {
        "run_id": run_id,
        "sensitivity": sensitivity,
        "stability": stability,
        "summary": summary,
        "recommendations": _generate_recommendations(sensitivity, stability),
    }


def _generate_recommendations(sensitivity: Dict, stability: Dict) -> List[str]:
    """Generate actionable recommendations based on diagnostics."""
    
    recommendations = []
    
    for market, sens in sensitivity.items():
        stab = stability.get(market, {})
        
        most_impactful = sens.get("most_impactful_layer", "unknown")
        least_impactful = sens.get("least_impactful_layer", "unknown")
        
        if most_impactful == "calibration":
            recommendations.append(
                f"{market}: Calibration layer dominates EV - verify calibration quality"
            )
        elif most_impactful == "league":
            recommendations.append(
                f"{market}: League normalization has highest impact - review league baselines"
            )
        elif most_impactful == "latent":
            recommendations.append(
                f"{market}: Latent state has highest impact - review regime detection"
            )
        
        most_unstable = stab.get("most_unstable_layer", "unknown")
        if most_unstable == "risk":
            recommendations.append(
                f"{market}: Risk layer is most unstable - review risk thresholds"
            )
        
        var_reduction = sens.get("variance_reduction_pct", 0)
        if var_reduction < 10:
            recommendations.append(
                f"{market}: Low variance reduction ({var_reduction:.1f}%) - layers may not be helping"
            )
    
    if not recommendations:
        recommendations.append("System looks well-calibrated across all layers")
    
    return recommendations