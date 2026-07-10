"""
Fits an isotonic calibrator on recent settled prediction_records for a market.

ARCHIVED, Phase 33: no longer called by anything. calibration_consumer.py's
CALIBRATION_DRIFT_DETECTED handler used to fit here and archive the result via
ModelRegistry.register_recalibration() -- but get_model_prediction() only applies
calibrator objects exposing .calibrate() (MarketCalibrator), and explicitly skips
the raw sklearn.isotonic.IsotonicRegression this function returns. That refit
path was therefore a real action with zero effect on served predictions --
fixed by pointing the drift handler at LeagueCalibrationEngine.fit_all()
instead (the Platt-scaling stack that actually reaches inference).
Phase 33 Task 2 compared both algorithms fresh on the same corruption-excluded
window and found them within noise of each other (Brier/ECE delta <0.01 on
all 4 markets) -- so this is kept for reference/a possible future ensemble,
not because it measured worse, but because building a second serving path for
a statistically-equivalent algorithm wasn't worth the complexity. The drift
MONITOR that triggers CALIBRATION_DRIFT_DETECTED (StateCalibrationEngine's
live_drift_ece, computed from PredictionRecord settlements) is unaffected by
this and remains correct -- only this refit ARTIFACT path was dead.

Extracted from backend/execution_engine.py during Phase 31 Part D.
"""

import logging

logger = logging.getLogger(__name__)


def fit_calibrator_for_market(market: str):
    """Fit an isotonic calibrator on recent settled prediction_records for a market.

    Returns (calibrator, metrics) tuple, or (None, None) if insufficient data.
    metrics includes brier_score and ece computed on post-calibration probabilities.
    """
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from src.storage.db import get_session
    from sqlalchemy import text

    try:
        with get_session() as s:
            # Use our_prob (raw model output) not calibrated_prob — fitting on
            # already-calibrated values creates a circular dependency that degrades quality.
            rows = s.execute(text("""
                SELECT our_prob, won FROM prediction_records
                WHERE market = :market AND settled = 1 AND our_prob IS NOT NULL AND won IS NOT NULL
                ORDER BY id DESC LIMIT 2000
            """), {"market": market}).fetchall()

        if len(rows) < 100:
            logger.warning("fit_calibrator_for_market: only %d settled rows for %s", len(rows), market)
            return None, None

        probs = np.array([r[0] for r in rows], dtype=float)
        outcomes = np.array([int(r[1]) for r in rows], dtype=float)

        # Chronological 80/20 split: fit on older data, evaluate on recent held-out.
        # Using random split on time-ordered data would leak future into training.
        split = int(len(probs) * 0.8)
        train_p, eval_p = probs[:split], probs[split:]
        train_o, eval_o = outcomes[:split], outcomes[split:]

        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(train_p, train_o)

        # Metrics on held-out eval set (out-of-sample)
        eval_cal = np.clip(calibrator.predict(eval_p), 0.01, 0.99)
        brier = float(np.mean((eval_cal - eval_o) ** 2))

        n_bins = 10
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (eval_cal >= bin_edges[i]) & (
                eval_cal <= bin_edges[i + 1] if i == n_bins - 1 else eval_cal < bin_edges[i + 1]
            )
            if np.sum(mask) == 0:
                continue
            ece += (np.sum(mask) / len(eval_cal)) * abs(np.mean(eval_o[mask]) - np.mean(eval_cal[mask]))

        metrics = {
            "brier_score": brier,
            # This calibrator's own held-out post-fit eval ECE — distinct from
            # StateCalibrationEngine's live_drift_ece (recent PredictionRecord
            # settlements, drives the drift alarm). Conflating the two under
            # one "ece" name across 94 versions was the Phase 27b root cause;
            # see the Separation Principle in docs/codebase_reference.md.
            "postfit_eval_ece": ece,
            "calibration_sample_size": len(rows),
            "eval_sample_size": len(eval_p),
        }
        return calibrator, metrics
    except Exception:
        logger.exception("fit_calibrator_for_market failed for %s", market)
        return None, None
