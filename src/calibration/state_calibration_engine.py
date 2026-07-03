"""
State Calibration Engine - Statistically self-correcting capital allocator.

Continuously measures and corrects:
- prediction accuracy drift
- probability calibration error
- risk model bias
- portfolio return divergence
- correlation misestimation

This layer ensures the system transitions from "structured decision system"
to "statistically self-correcting capital allocator".
"""

import logging
import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import numpy as np

from src.alerts.event_bus import event_bus, Events
from src.portfolio.state.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)


@dataclass
class CalibrationMetrics:
    """Calibration metrics for a single market.

    live_drift_ece is the drift-monitor's ECE — computed here, from recently
    settled PredictionRecord outcomes, over StateCalibrationEngine's rolling
    window. It is NOT the same number as ModelVersion.ece (the calibrator's
    own held-out post-fit eval ECE, computed by
    backend/execution_engine.py::_fit_calibrator_for_market). Phase 27b found
    these two conflated under one name ("ece") for 94 versions: this one
    fires CALIBRATION_DRIFT_DETECTED and should track live prediction
    accuracy; the other describes how well a specific calibrator fit its own
    holdout split. See docs/codebase_reference.md's Separation Principle
    section for the full case study.
    """
    market: str
    brier_score: float = 0.0
    live_drift_ece: float = 0.0  # Expected Calibration Error, live drift monitor
    reliability_slope: float = 1.0
    reliability_intercept: float = 0.0
    sample_count: int = 0
    last_updated: str = ""


@dataclass
class CalibrationReport:
    """Comprehensive calibration report."""
    timestamp: str = ""
    markets: dict[str, CalibrationMetrics] = field(default_factory=dict)
    overall_calibration_error: float = 0.0
    risk_bias: float = 0.0
    portfolio_drift: float = 0.0
    correlation_error: float = 0.0
    recommended_adjustments: dict = field(default_factory=dict)
    requires_retrain: bool = False
    requires_policy_adjustment: bool = False


@dataclass
class PredictionOutcome:
    """A prediction with its actual outcome."""
    fixture_id: int
    market: str
    predicted_prob: float
    actual_outcome: int  # 0 or 1
    odds: float = 0.0
    timestamp: str = ""


class StateCalibrationEngine:
    """
    Calibration and convergence layer.

    Measures system alignment with real-world outcomes and provides
    feedback to PortfolioEngine, RiskEngine, and PolicyEngine.
    """

    # Cooldown prevents repeated retrain/recalibrate events for the same market
    _RETRAIN_COOLDOWN_HOURS = 24
    _RECALIBRATE_COOLDOWN_HOURS = 6

    def __init__(self, reports_dir: str = "/opt/projects/bootball/reports/calibration"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self._prediction_outcomes: list[PredictionOutcome] = []
        self._portfolio_history: list[PortfolioState] = []
        self._monte_carlo_comparisons: list[dict] = []
        self._last_triggered: dict[tuple, datetime] = {}

        # Thresholds for alerts
        self.brier_threshold = 0.25
        self.live_drift_ece_threshold = 0.10
        self.risk_bias_threshold = 0.15
        self.portfolio_drift_threshold = 0.10
        self.correlation_error_threshold = 0.20

        logger.info("[CALIBRATION] State Calibration Engine initialized")

    def _cooldown_ok(self, market: str, action: str, hours: int) -> bool:
        """Return True if enough time has passed since last trigger for (market, action)."""
        last = self._last_triggered.get((market, action))
        if last is None:
            return True
        return (datetime.utcnow() - last).total_seconds() > hours * 3600

    def _mark_triggered(self, market: str, action: str) -> None:
        self._last_triggered[(market, action)] = datetime.utcnow()
    
    # Cap on the in-memory rolling window (across all markets combined) so a
    # long-lived process doesn't accumulate settlements forever. ~1250/market
    # average at 4 markets — plenty for a "recent settlements" ECE read.
    _MAX_PREDICTION_OUTCOMES = 5000

    def add_prediction_outcome(
        self,
        fixture_id: int,
        market: str,
        predicted_prob: float,
        actual_outcome: int,
        odds: float = 0.0
    ) -> None:
        """Add a prediction outcome for calibration tracking."""
        outcome = PredictionOutcome(
            fixture_id=fixture_id,
            market=market,
            predicted_prob=predicted_prob,
            actual_outcome=actual_outcome,
            odds=odds,
            timestamp=datetime.utcnow().isoformat()
        )
        self._prediction_outcomes.append(outcome)
        if len(self._prediction_outcomes) > self._MAX_PREDICTION_OUTCOMES:
            self._prediction_outcomes = self._prediction_outcomes[-self._MAX_PREDICTION_OUTCOMES:]

        logger.debug(f"[CALIBRATION] Added outcome: {market} pred={predicted_prob:.2f} actual={actual_outcome}")

    def ingest_recent_prediction_outcomes(self, markets: tuple[str, ...] = ("h2h", "btts", "ou25", "ou15"),
                                           batch_limit: int = 500) -> int:
        """Pull newly-settled PredictionRecord rows into the live-drift window.

        Phase 28: this replaces the Phase 27b "ghost alarm" — the drift check
        used to read the most recent 100 settled PlacedBet rows (frozen since
        betting closed 2026-06-11, reproducing the identical ECE=0.2807167287
        forever). Per the Separation Principle, the prediction layer's drift
        monitor must read the prediction layer's own live outcomes only.

        Dedup is a persistent per-market high-water mark (calibration_drift_state
        table) — not an in-memory set — so a process restart resumes from
        where it left off instead of replaying already-consumed settlements.

        Uses PredictionRecord.calibrated_prob (falling back to our_prob if
        unset) against `won`, matching what the prediction layer actually
        served, same convention as the PlacedBet path it replaces.

        Returns the number of new outcomes ingested this call.
        """
        from sqlalchemy import select as _select
        from src.storage.db import get_session as _get_session
        from src.storage.models import PredictionRecord, CalibrationDriftState

        total_ingested = 0
        with _get_session() as session:
            for market in markets:
                state = session.get(CalibrationDriftState, market)
                last_seen_id = state.last_seen_prediction_id if state else 0

                rows = session.execute(
                    _select(PredictionRecord)
                    .where(PredictionRecord.market == market)
                    .where(PredictionRecord.settled == True)
                    .where(PredictionRecord.won.isnot(None))
                    .where(PredictionRecord.id > last_seen_id)
                    .order_by(PredictionRecord.id.asc())
                    .limit(batch_limit)
                ).scalars().all()

                if not rows:
                    continue

                for row in rows:
                    predicted_prob = row.calibrated_prob if row.calibrated_prob is not None else row.our_prob
                    if predicted_prob is None:
                        continue
                    self.add_prediction_outcome(
                        fixture_id=row.fixture_id,
                        market=market,
                        predicted_prob=predicted_prob,
                        actual_outcome=1 if row.won else 0,
                        odds=row.odds_decimal or 0.0,
                    )
                    total_ingested += 1

                new_last_seen = rows[-1].id
                if state:
                    state.last_seen_prediction_id = new_last_seen
                    state.updated_at = datetime.utcnow()
                else:
                    session.add(CalibrationDriftState(
                        market=market,
                        last_seen_prediction_id=new_last_seen,
                        updated_at=datetime.utcnow(),
                    ))

            session.commit()

        return total_ingested
    
    def add_portfolio_state(self, state: PortfolioState) -> None:
        """Add a portfolio state snapshot."""
        self._portfolio_history.append(state)
        
        # Keep only last 100 states
        if len(self._portfolio_history) > 100:
            self._portfolio_history = self._portfolio_history[-100:]
    
    def add_monte_carlo_comparison(
        self,
        predicted_metrics: dict,
        actual_metrics: dict
    ) -> None:
        """Add a Monte Carlo vs actual comparison."""
        comparison = {
            "predicted": predicted_metrics,
            "actual": actual_metrics,
            "timestamp": datetime.utcnow().isoformat()
        }
        self._monte_carlo_comparisons.append(comparison)
        
        if len(self._monte_carlo_comparisons) > 50:
            self._monte_carlo_comparisons = self._monte_carlo_comparisons[-50:]
    
    def compute_calibration_metrics(self, market: str) -> CalibrationMetrics:
        """Compute calibration metrics for a market."""
        outcomes = [o for o in self._prediction_outcomes if o.market == market]
        
        if len(outcomes) < 10:
            return CalibrationMetrics(market=market, sample_count=len(outcomes))
        
        # Extract predictions and outcomes
        probs = np.array([o.predicted_prob for o in outcomes])
        actuals = np.array([o.actual_outcome for o in outcomes])
        
        # Brier score: (p - o)^2 averaged
        brier = float(np.mean((probs - actuals) ** 2))
        
        # Expected Calibration Error (ECE)
        n_bins = 10
        bins = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (probs >= bins[i]) & (probs < bins[i + 1])
            if np.sum(mask) > 0:
                bin_mean = np.mean(probs[mask])
                bin_acc = np.mean(actuals[mask])
                ece += (np.sum(mask) / len(outcomes)) * abs(bin_mean - bin_acc)
        
        # Reliability curve (linear regression)
        if len(outcomes) > 20:
            slope, intercept = np.polyfit(probs, actuals, 1)
        else:
            slope, intercept = 1.0, 0.0
        
        return CalibrationMetrics(
            market=market,
            brier_score=brier,
            live_drift_ece=ece,
            reliability_slope=float(slope),
            reliability_intercept=float(intercept),
            sample_count=len(outcomes),
            last_updated=datetime.utcnow().isoformat()
        )
    
    def compute_risk_mispricing(self) -> float:
        """Compute risk model bias (predicted vs realized drawdown)."""
        if not self._monte_carlo_comparisons:
            return 0.0
        
        biases = []
        for comp in self._monte_carlo_comparisons:
            pred_dd = comp["predicted"].get("max_drawdown", 0)
            actual_dd = comp["actual"].get("realized_drawdown", 0)
            
            if pred_dd > 0:
                bias = (actual_dd - pred_dd) / pred_dd
                biases.append(bias)
        
        if not biases:
            return 0.0
        
        return float(np.mean(biases))
    
    def compute_portfolio_drift(self) -> float:
        """Compute portfolio drift (expected ROI vs realized ROI)."""
        if len(self._portfolio_history) < 2:
            return 0.0
        
        # Compare recent ROI to expected
        recent_states = self._portfolio_history[-10:]
        
        realized_rois = [s.roi for s in recent_states]
        
        if not realized_rois:
            return 0.0
        
        # Expected ROI from predictions
        expected_roi = np.mean([s.realized_pnl / max(s.realized_pnl + s.unrealized_pnl, 1) 
                              for s in recent_states 
                              if s.realized_pnl + s.unrealized_pnl > 0])
        
        realized = np.mean(realized_rois)
        
        drift = realized - expected_roi if expected_roi > 0 else 0.0
        
        return float(drift)
    
    def compute_correlation_error(self) -> float:
        """Compute correlation estimation error."""
        if len(self._portfolio_history) < 5:
            return 0.0
        
        # Check if correlation clusters are misestimated
        recent = self._portfolio_history[-20:]
        
        # Get market exposures
        all_markets = set()
        for s in recent:
            all_markets.update(s.exposure_by_market.keys())
        
        if len(all_markets) < 2:
            return 0.0
        
        # Measure co-movement
        errors = []
        market_pairs = [("btts", "ou25"), ("ou25", "ou15"), ("h2h", "btts")]
        
        for m1, m2 in market_pairs:
            exposures = [(s.exposure_by_market.get(m1, 0), s.exposure_by_market.get(m2, 0)) 
                        for s in recent]
            
            if len(exposures) > 5:
                e1 = np.array([e[0] for e in exposures])
                e2 = np.array([e[1] for e in exposures])
                
                if np.std(e1) > 0 and np.std(e2) > 0:
                    corr = np.corrcoef(e1, e2)[0, 1]
                    # Expected correlation (from CorrelationEngine)
                    expected = {"btts_ou25": 0.65, "ou25_ou15": 0.70, "h2h_btts": 0.20}
                    key = f"{m1}_{m2}"
                    exp = expected.get(key, 0.5)
                    
                    errors.append(abs(corr - exp))
        
        return float(np.mean(errors)) if errors else 0.0
    
    def generate_report(self) -> CalibrationReport:
        """Generate comprehensive calibration report."""
        logger.info("[CALIBRATION] Generating calibration report")
        
        report = CalibrationReport(
            timestamp=datetime.utcnow().isoformat()
        )
        
        # Compute metrics per market
        markets = ["h2h", "btts", "ou25", "ou15"]
        for market in markets:
            metrics = self.compute_calibration_metrics(market)
            report.markets[market] = metrics
        
        # Overall calibration error (weighted average)
        total_weight = sum(m.sample_count for m in report.markets.values())
        if total_weight > 0:
            report.overall_calibration_error = sum(
                m.brier_score * m.sample_count for m in report.markets.values()
            ) / total_weight
        
        # Risk mispricing
        report.risk_bias = self.compute_risk_mispricing()
        
        # Portfolio drift
        report.portfolio_drift = self.compute_portfolio_drift()
        
        # Correlation error
        report.correlation_error = self.compute_correlation_error()
        
        # Generate recommendations
        report.recommended_adjustments = self._generate_adjustments(report)
        
        # Determine if action needed
        report.requires_retrain = report.overall_calibration_error > self.brier_threshold
        report.requires_policy_adjustment = abs(report.risk_bias) > self.risk_bias_threshold
        
        # Emit events
        self._emit_calibration_events(report)
        
        # Save report
        self._save_report(report)
        
        logger.info(f"[CALIBRATION] Report: cal_error={report.overall_calibration_error:.3f}, "
                   f"risk_bias={report.risk_bias:.3f}, drift={report.portfolio_drift:.3f}")
        
        return report
    
    def _generate_adjustments(self, report: CalibrationReport) -> dict:
        """Generate recommended adjustments based on calibration report."""
        adjustments = {}
        
        # Market-specific adjustments
        market_adjustments = {}
        for market, metrics in report.markets.items():
            if metrics.sample_count < 10:
                continue
            
            if metrics.brier_score > self.brier_threshold:
                market_adjustments[market] = {
                    "action": "retrain",
                    "reason": f"Brier score {metrics.brier_score:.3f} exceeds threshold",
                    "priority": "high"
                }
            elif metrics.live_drift_ece > self.live_drift_ece_threshold:
                market_adjustments[market] = {
                    "action": "recalibrate",
                    "reason": f"live_drift_ece {metrics.live_drift_ece:.3f} exceeds threshold",
                    "priority": "medium"
                }
        
        adjustments["market_adjustments"] = market_adjustments
        
        # Risk adjustments
        if abs(report.risk_bias) > self.risk_bias_threshold:
            adjustments["risk_adjustment"] = {
                "action": "adjust_lambda",
                "reason": f"Risk bias {report.risk_bias:.3f} exceeds threshold",
                "direction": "increase" if report.risk_bias > 0 else "decrease",
                "magnitude": abs(report.risk_bias)
            }
        
        # Portfolio adjustments
        if abs(report.portfolio_drift) > self.portfolio_drift_threshold:
            adjustments["portfolio_adjustment"] = {
                "action": "reweight",
                "reason": f"Portfolio drift {report.portfolio_drift:.3f} exceeds threshold",
                "direction": "increase_exposure" if report.portfolio_drift < 0 else "decrease_exposure"
            }
        
        # Correlation adjustments
        if report.correlation_error > self.correlation_error_threshold:
            adjustments["correlation_adjustment"] = {
                "action": "recompute_correlation",
                "reason": f"Correlation error {report.correlation_error:.3f} exceeds threshold"
            }
        
        return adjustments
    
    def _emit_calibration_events(self, report: CalibrationReport) -> None:
        """Emit calibration events.

        Per-market events drive actual recalibration / retraining via consumers.
        A cooldown per (market, action) prevents event spam across frequent report cycles.
        """
        market_adjustments = report.recommended_adjustments.get("market_adjustments", {})

        for market, adj in market_adjustments.items():
            m = report.markets.get(market)
            action = adj.get("action")

            if action == "recalibrate" and self._cooldown_ok(
                market, "recalibrate", self._RECALIBRATE_COOLDOWN_HOURS
            ):
                self._mark_triggered(market, "recalibrate")
                event_bus.emit(Events.CALIBRATION_DRIFT_DETECTED, {
                    "market": market,
                    "calibration_error": m.brier_score if m else 0,
                    "live_drift_ece": m.live_drift_ece if m else 0,
                    "reason": adj.get("reason", "live_drift_ece_threshold_exceeded"),
                    "timestamp": report.timestamp,
                    "summary": f"Recalibrate {market}: {adj.get('reason', '')}",
                })

            elif action == "retrain" and self._cooldown_ok(
                market, "retrain", self._RETRAIN_COOLDOWN_HOURS
            ):
                self._mark_triggered(market, "retrain")
                event_bus.emit(Events.MODEL_TREND, {
                    "market": market,
                    "direction": "degrading",
                    "confidence": "statistically_meaningful",
                    "brier_score": m.brier_score if m else 0,
                    "live_drift_ece": m.live_drift_ece if m else 0,
                    "reason": adj.get("reason", "brier_threshold_exceeded"),
                    "timestamp": report.timestamp,
                    "summary": f"Retrain {market}: {adj.get('reason', '')}",
                })

        if report.requires_policy_adjustment:
            event_bus.emit(Events.RISK_MODEL_CORRECTED, {
                "risk_bias": report.risk_bias,
                "adjustment": report.recommended_adjustments.get("risk_adjustment"),
                "timestamp": report.timestamp,
            })

        if report.portfolio_drift != 0:
            event_bus.emit(Events.PORTFOLIO_REWEIGHTING_SUGGESTED, {
                "drift": report.portfolio_drift,
                "adjustment": report.recommended_adjustments.get("portfolio_adjustment"),
                "timestamp": report.timestamp,
            })

        event_bus.emit(Events.CALIBRATION_REPORT_READY, {
            "markets": {k: {"brier": v.brier_score, "live_drift_ece": v.live_drift_ece}
                       for k, v in report.markets.items()},
            "overall_error": report.overall_calibration_error,
            "risk_bias": report.risk_bias,
            "portfolio_drift": report.portfolio_drift,
            "correlation_error": report.correlation_error,
            "timestamp": report.timestamp,
        })
    
    def _save_report(self, report: CalibrationReport) -> None:
        """Save calibration report to disk."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath = self.reports_dir / f"calibration_{timestamp}.json"
        
        data = {
            "timestamp": report.timestamp,
            "markets": {
                k: {
                    "brier_score": v.brier_score,
                    "live_drift_ece": v.live_drift_ece,
                    "reliability_slope": v.reliability_slope,
                    "reliability_intercept": v.reliability_intercept,
                    "sample_count": v.sample_count,
                    "last_updated": v.last_updated,
                }
                for k, v in report.markets.items()
            },
            "overall_calibration_error": report.overall_calibration_error,
            "risk_bias": report.risk_bias,
            "portfolio_drift": report.portfolio_drift,
            "correlation_error": report.correlation_error,
            "recommended_adjustments": report.recommended_adjustments,
            "requires_retrain": report.requires_retrain,
            "requires_policy_adjustment": report.requires_policy_adjustment,
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"[CALIBRATION] Report saved to {filepath}")
    
    def apply_feedback_to_portfolio(self, weights: dict) -> dict:
        """Apply calibration feedback to portfolio weights."""
        report = self.generate_report()
        
        adjustments = report.recommended_adjustments
        market_adj = adjustments.get("market_adjustments", {})
        
        # Adjust weights based on calibration
        adjusted = weights.copy()
        
        for market, adj in market_adj.items():
            if adj["action"] == "retrain" and market in adjusted:
                # Reduce weight for underperforming market
                adjusted[market] *= 0.7
                logger.info(f"[CALIBRATION] Reducing {market} weight to {adjusted[market]:.2f}")
            elif adj["action"] == "recalibrate" and market in adjusted:
                # Slight reduction
                adjusted[market] *= 0.9
        
        # Normalize
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v/total for k, v in adjusted.items()}
        
        return adjusted
    
    def apply_feedback_to_risk(self, lambda_val: float) -> float:
        """Apply calibration feedback to risk lambda."""
        report = self.generate_report()
        
        risk_adj = report.recommended_adjustments.get("risk_adjustment")
        if not risk_adj:
            return lambda_val
        
        direction = risk_adj.get("direction", "increase")
        magnitude = risk_adj.get("magnitude", 0)
        
        if direction == "increase":
            new_lambda = lambda_val * (1 + magnitude)
        else:
            new_lambda = lambda_val * (1 - magnitude)
        
        # Clamp
        new_lambda = max(0.5, min(3.0, new_lambda))
        
        logger.info(f"[CALIBRATION] Adjusting lambda: {lambda_val:.2f} -> {new_lambda:.2f}")
        
        return new_lambda
    
    def apply_feedback_to_correlation(self, correlation_matrix: dict) -> dict:
        """Apply calibration feedback to correlation estimates."""
        report = self.generate_report()
        
        if report.correlation_error <= self.correlation_error_threshold:
            return correlation_matrix
        
        # Adjust correlation estimates toward observed
        # This is a simplified implementation
        adjusted = correlation_matrix.copy()
        
        logger.info(f"[CALIBRATION] Correlation error detected: {report.correlation_error:.3f}")
        
        return adjusted


# Global instance
_engine: Optional[StateCalibrationEngine] = None


def get_state_calibration_engine() -> StateCalibrationEngine:
    """Get global state calibration engine."""
    global _engine
    if _engine is None:
        _engine = StateCalibrationEngine()
    return _engine
