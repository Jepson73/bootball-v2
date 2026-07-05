"""
V2 prediction cycle — Phase 31 Part C.

This is the V2-owned replacement for AgentCoordinator.run_cycle()'s live core. A full
read of src/agents/coordinator.py (see OWNERSHIP.md) found that of its ~1050 lines,
only two things ever have a live effect since betting closed (Phase 8, bot_enabled=False):

1. Fetch NS fixtures -> UnifiedPredictionService.generate_with_fixture_data() ->
   save_predictions(). This is the actual product: PredictionRecord rows.
2. state_calibration_engine.ingest_recent_prediction_outcomes() + generate_report(),
   which fires CALIBRATION_DRIFT_DETECTED for real recalibration. AgentCoordinator was
   this function's only caller anywhere in the codebase.

Everything else in the old coordinator cycle (RiskManagerAgent, ExecutionStrategistAgent,
PortfolioEngine, AdversaryAgent, PolicyEngine, PlacedBet writes, PerformanceEvaluator/
WeightOptimizer/EventReplay, MetaPolicyEngine, ClosedLoopValidationEngine) operated on a
betting ledger that has taken zero new rows since 2026-06-07 and is not carried forward.
"""

import logging
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _fetch_ns_fixtures():
    """Fetch upcoming (status=NS) fixtures as detached stubs, avoiding session expiry."""
    from src.storage.db import get_session
    from src.storage.models import Fixture

    with get_session() as s:
        rows = s.execute(
            select(Fixture)
            .where(Fixture.status == "NS")
            .where(Fixture.date >= datetime.utcnow())
            .order_by(Fixture.date.asc())
        ).scalars().all()

        class FixtureStub:
            def __init__(self, f):
                self.id = f.id
                self.home_team_id = f.home_team_id
                self.away_team_id = f.away_team_id
                self.league_id = f.league_id
                self.date = f.date
                self.status = f.status

        return [FixtureStub(f) for f in rows]


def generate_predictions(save: bool = True, run_id: str = None) -> dict:
    """Fetch NS fixtures and generate predictions.

    save=False (dry-run) skips save_predictions() entirely, for the Part C parity
    comparison against AgentCoordinator's own output before any cutover.
    """
    from src.prediction.unified_prediction_service import get_unified_prediction_service

    fixtures = _fetch_ns_fixtures()
    if not fixtures:
        logger.warning("[V2_PREDICTION_CYCLE] No NS fixtures available")
        return {"fixtures": 0, "predictions": [], "saved_ids": []}

    logger.info(f"[V2_PREDICTION_CYCLE] Fetched {len(fixtures)} NS fixtures")

    prediction_service = get_unified_prediction_service()
    predictions = prediction_service.generate_with_fixture_data(fixtures)

    if not predictions:
        logger.error("[V2_PREDICTION_CYCLE] PIPELINE DEAD: no predictions generated")
        return {"fixtures": len(fixtures), "predictions": [], "saved_ids": []}

    saved_ids = []
    if save:
        saved_ids = prediction_service.save_predictions(predictions, run_id=run_id)
        logger.info(f"[V2_PREDICTION_CYCLE] Saved {len(saved_ids)} predictions (run_id={run_id})")
    else:
        logger.info(f"[V2_PREDICTION_CYCLE] DRY RUN — generated {len(predictions)} predictions, not saved")

    return {"fixtures": len(fixtures), "predictions": predictions, "saved_ids": saved_ids}


def run_calibration_ingest() -> dict:
    """Ingest recent prediction outcomes into the live-drift monitor and, if there
    were new outcomes, generate a report — the sole path that fires
    CALIBRATION_DRIFT_DETECTED. AgentCoordinator was the only caller of this before
    Phase 31; this is its new V2 home."""
    from src.calibration.state_calibration_engine import get_state_calibration_engine

    engine = get_state_calibration_engine()
    new_outcomes_count = engine.ingest_recent_prediction_outcomes()
    logger.info(f"[V2_PREDICTION_CYCLE] Ingested {new_outcomes_count} new prediction outcomes")

    report = None
    if new_outcomes_count:
        report = engine.generate_report()
        logger.info(
            f"[V2_PREDICTION_CYCLE] Calibration report generated: "
            f"error={report.overall_calibration_error:.3f}"
        )

    return {"new_outcomes": new_outcomes_count, "report": report}


def run_prediction_cycle(save: bool = True, run_id: str = None) -> dict:
    """The full V2 prediction cycle: generate + save predictions, then run the
    live-drift calibration ingest. This is the entire replacement for
    AgentCoordinator.run_cycle() — see module docstring and OWNERSHIP.md."""
    if run_id is None:
        import uuid
        run_id = str(uuid.uuid4())[:8]

    logger.info(f"[V2_PREDICTION_CYCLE] Starting cycle run_id={run_id}")

    prediction_result = generate_predictions(save=save, run_id=run_id)

    calibration_result = {"new_outcomes": 0, "report": None}
    if save:
        # Calibration reads settled PredictionRecord outcomes — running it in dry-run
        # mode would just re-ingest the same outcomes a real cycle already will.
        try:
            calibration_result = run_calibration_ingest()
        except Exception:
            logger.exception("[V2_PREDICTION_CYCLE] Calibration ingest failed (non-fatal)")

    return {
        "run_id": run_id,
        "fixtures": prediction_result["fixtures"],
        "predictions": len(prediction_result["predictions"]),
        "saved_ids": prediction_result["saved_ids"],
        "calibration_new_outcomes": calibration_result["new_outcomes"],
    }
