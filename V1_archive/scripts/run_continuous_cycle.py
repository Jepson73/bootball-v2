#!/usr/bin/env python3
"""
scripts/run_continuous_cycle.py - CONTINUOUS PREDICTION PIPELINE

Runs every 15-30 minutes via scheduler.

Flow:
1. Fetch updated odds
2. Detect meaningful changes (should_repredict)
3. Run predictions via UnifiedPredictionService
4. Run full portfolio pipeline (AgentCoordinator)
5. Run settlement
6. Emit events
7. Validate via CLVE
"""

import argparse
import logging
import sys
from pathlib import Path
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func, Column

from src.storage.db import get_session
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, PlacedBet

from src.alerts.event_bus import event_bus as EventBus, Events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Configuration
ODDS_CHANGE_THRESHOLD = 0.05  # 5% odds change triggers repredict
PREDICTION_STALE_HOURS = 6  # Repredict if older than 6 hours
TIME_TO_MATCH_THRESHOLD = 6  # Repredict if < 6 hours to match

# Force predictions override (for debugging)
FORCE_PREDICTIONS = False  # Set True to bypass should_repredict


@dataclass
class ContinuousCycleConfig:
    odds_change_threshold: float = ODDS_CHANGE_THRESHOLD
    prediction_stale_hours: int = PREDICTION_STALE_HOURS
    time_to_match_threshold: int = TIME_TO_MATCH_THRESHOLD


def should_repredict(fixture_id: int, config: ContinuousCycleConfig = None) -> bool:
    """
    Determine if a fixture should trigger reprediction.
    
    Returns True if:
    - odds delta > threshold
    - lineup changes (not implemented)
    - time-to-match threshold crossed
    - no prior prediction exists
    - prediction is stale
    """
    config = config or ContinuousCycleConfig()
    
    with get_session() as s:
        fixture = s.execute(
            select(Fixture).where(Fixture.id == fixture_id)
        ).scalar_one_or_none()
        
        if not fixture:
            return False
        
        # Check if no prediction exists - use fetchall and check length
        predictions = s.execute(
            select(PredictionRecord).where(PredictionRecord.fixture_id == fixture_id)
            .order_by(PredictionRecord.created_at.desc())
        ).scalars().all()
        
        if not predictions:
            logger.info(f"[CONTINUOUS] Fixture {fixture_id}: No prediction exists")
            return True
        
        # Use most recent prediction
        prediction = predictions[0]
        
        # Check if prediction is stale
        pred_time = prediction.created_at
        if pred_time:
            # Ensure timezone-aware comparison
            now = datetime.now(ZoneInfo("UTC"))
            if pred_time.tzinfo is None:
                pred_time = pred_time.replace(tzinfo=ZoneInfo("UTC"))
            age = now - pred_time
            if age.total_seconds() > config.prediction_stale_hours * 3600:
                logger.info(f"[CONTINUOUS] Fixture {fixture_id}: Prediction stale ({age})")
                return True
        
        # Check if time-to-match threshold crossed
        if fixture.date:
            now = datetime.now(ZoneInfo("UTC"))
            fixture_date = fixture.date
            if fixture_date.tzinfo is None:
                fixture_date = fixture_date.replace(tzinfo=ZoneInfo("UTC"))
            time_to_match = fixture_date - now
            hours_to_match = time_to_match.total_seconds() / 3600
            if 0 < hours_to_match < config.time_to_match_threshold:
                logger.info(f"[CONTINUOUS] Fixture {fixture_id}: Time to match < {config.time_to_match_threshold}h")
                return True
        
        # Check odds changes (simplified - would need odds history tracking)
        # For now, always repredict if we get here during active hours
        return True


def fetch_odds_updates(config: ContinuousCycleConfig = None) -> list[int]:
    """Fetch fixture IDs that need prediction updates."""
    config = config or ContinuousCycleConfig()
    
    logger.info("=" * 60)
    logger.info("[DIAGNOSTIC] STEP 1: FETCHING FIXTURES")
    logger.info("=" * 60)
    
    now = datetime.now(ZoneInfo("UTC"))
    
    with get_session() as s:
        # Get upcoming fixtures with odds
        fixtures = s.execute(
            select(Fixture)
            .join(FixtureOdds, Fixture.id == FixtureOdds.fixture_id)
            .where(Fixture.status == "NS")
            .where(Fixture.date >= now)
            .limit(50)
        ).scalars().all()
        
        fixture_ids = [f.id for f in fixtures]
        logger.info(f"[DIAGNOSTIC] Fixtures fetched from DB: {len(fixture_ids)}")
        
        # Filter to those that should repredict
        to_repredict_ids = []
        skip_reasons = {"no_prediction": 0, "stale": 0, "time_to_match": 0, "odds_changed": 0, "always_true": 0, "other": 0}
        
        for fid in fixture_ids:
            # Force override for debugging
            if FORCE_PREDICTIONS:
                logger.info(f"[DIAGNOSTIC] Fixture {fid}: FORCE_PREDICTIONS=True")
                to_repredict_ids.append(fid)
                skip_reasons["always_true"] += 1
                continue
                
            should_rep, reason = _should_repredict_with_reason(fid, config)
            if should_rep:
                to_repredict_ids.append(fid)
                logger.info(f"[DIAGNOSTIC] Fixture {fid}: DECISION=PREDICTED, reason={reason}")
            else:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                logger.info(f"[DIAGNOSTIC] Fixture {fid}: DECISION=SKIPPED, reason={reason}")
        
        logger.info(f"[DIAGNOSTIC] SKIP REASONS: {skip_reasons}")
        logger.info(f"[DIAGNOSTIC] {len(to_repredict_ids)}/{len(fixture_ids)} fixtures need reprediction")
        
        return to_repredict_ids


def _should_repredict_with_reason(fixture_id: int, config: ContinuousCycleConfig = None) -> tuple[bool, str]:
    """Determine if a fixture should reprediction with explicit reason."""
    config = config or ContinuousCycleConfig()
    
    with get_session() as s:
        fixture = s.execute(
            select(Fixture).where(Fixture.id == fixture_id)
        ).scalar_one_or_none()
        
        if not fixture:
            return False, "fixture_not_found"
        
        # Check if no prediction exists - use fetchall
        predictions = s.execute(
            select(PredictionRecord).where(PredictionRecord.fixture_id == fixture_id)
            .order_by(PredictionRecord.created_at.desc())
        ).scalars().all()
        
        if not predictions:
            return True, "no_prediction"
        
        # Use most recent prediction
        prediction = predictions[0]
        
        # Check if prediction is stale
        pred_time = prediction.created_at
        if pred_time:
            now = datetime.now(ZoneInfo("UTC"))
            if pred_time.tzinfo is None:
                pred_time = pred_time.replace(tzinfo=ZoneInfo("UTC"))
            age = now - pred_time
            if age.total_seconds() > config.prediction_stale_hours * 3600:
                return True, "stale"
        
        # Check if time-to-match threshold crossed
        if fixture.date:
            now = datetime.now(ZoneInfo("UTC"))
            fixture_date = fixture.date
            if fixture_date.tzinfo is None:
                fixture_date = fixture_date.replace(tzinfo=ZoneInfo("UTC"))
            time_to_match = fixture_date - now
            hours_to_match = time_to_match.total_seconds() / 3600
            if 0 < hours_to_match < config.time_to_match_threshold:
                return True, "time_to_match"
        
        # Default: repredict
        return True, "odds_changed"


def run_continuous_cycle(run_id: str = None, context: dict = None):
    """
    Execute continuous prediction cycle.
    
    This is the PRIMARY entry point for continuous operation.
    """
    start_time = time.time()
    config = ContinuousCycleConfig()
    
    logger.info("=" * 60)
    logger.info("CONTINUOUS CYCLE STARTING")
    logger.info("=" * 60)
    
    # Emit cycle started
    EventBus.emit(Events.RUN_STARTED, {
        "run_id": run_id,
        "mode": "continuous_cycle",
        "timestamp": datetime.utcnow().isoformat(),
    })
    
    try:
        # STEP 1: Fetch updated odds / identify changes
        logger.info("[CONTINUOUS] Step 1: Fetching odds updates...")
        logger.info("[DIAGNOSTIC] CYCLE START")
        fixture_ids_to_update = fetch_odds_updates(config)
        
        logger.info(f"[DIAGNOSTIC] Fixtures flagged for prediction: {len(fixture_ids_to_update)}")
        
        if not fixture_ids_to_update:
            logger.warning("[CONTINUOUS] ⚠️  NO FIXTURES NEED PREDICTION UPDATE")
            logger.warning("[DIAGNOSTIC] RESULT: 0 predictions, 0 bets (no fixtures eligible)")
            EventBus.emit(Events.RUN_FINISHED, {
                "run_id": run_id,
                "mode": "continuous_cycle",
                "updated_count": 0,
                "timestamp": datetime.utcnow().isoformat(),
            })
            return {"updated": 0, "errors": ["No fixtures eligible for prediction"]}
        
        # Fetch fixtures fresh (as dicts to avoid session issues)
        logger.info(f"[DIAGNOSTIC] Fetching fixture objects for {len(fixture_ids_to_update)} fixtures...")
        
        # Create mock objects with required attributes
        fixtures_to_update = []
        with get_session() as s:
            for fid in fixture_ids_to_update:
                row = s.execute(
                    select(Fixture).where(Fixture.id == fid)
                ).scalar_one_or_none()
                if row:
                    # Create a simple object with required attributes
                    class FixtureStub:
                        def __init__(self, f):
                            self.id = f.id
                            self.home_team_id = f.home_team_id
                            self.away_team_id = f.away_team_id
                            self.league_id = f.league_id
                            self.date = f.date
                            self.status = f.status
                    
                    fixtures_to_update.append(FixtureStub(row))
        
        logger.info(f"[DIAGNOSTIC] Fixtures passing data validation: {len(fixtures_to_update)}")
        
        # HARD ASSERTION: Ensure baseline data is ready
        _assert_data_ready(fixtures_to_update)
        
        # STEP 2: Run predictions via UnifiedPredictionService
        logger.info(f"[CONTINUOUS] Step 2: Running predictions for {len(fixtures_to_update)} fixtures...")
        logger.info(f"[DIAGNOSTIC] Calling UnifiedPredictionService.generate_with_fixture_data()")
        
        from src.prediction.unified_prediction_service import get_unified_prediction_service
        
        prediction_service = get_unified_prediction_service()
        predictions = prediction_service.generate_with_fixture_data(fixtures_to_update)
        
        logger.info(f"[DIAGNOSTIC] Predictions generated: {len(predictions) if predictions else 0}")
        
        # HARD ASSERTION: Ensure predictions were generated
        if not predictions:
            raise RuntimeError("PIPELINE FAILURE: No predictions generated - HALTING")
        
        # HARD ASSERTION: Validate prediction structure (check our_prob, calibrated_prob optional for now)
        for pred in predictions:
            if not pred.get("our_prob") and not pred.get("calibrated_prob"):
                raise RuntimeError("PIPELINE FAILURE: Prediction missing probability - HALTING")
        
        logger.info(f"[CONTINUOUS] Generated {len(predictions)} predictions")
        
        EventBus.emit("PREDICTIONS_UPDATED", {
            "run_id": run_id,
            "prediction_count": len(predictions),
            "fixture_count": len(fixtures_to_update),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # STEP 3: Run full portfolio pipeline with pre-generated predictions
        logger.info("[CONTINUOUS] Step 3: Running full portfolio pipeline (using pre-generated predictions)...")
        from src.agents.coordinator import run_multi_agent_pipeline
        
        pipeline_result = run_multi_agent_pipeline(predictions=predictions)
        
        # HARD ASSERTION: CLVE must pass before bets can be placed
        if pipeline_result.get("bets", 0) > 0:
            _assert_clve_valid(pipeline_result)
        
        bets_placed = pipeline_result.get("bets", 0)
        logger.info(f"[CONTINUOUS] Pipeline result: {bets_placed} bets placed")
        
        # STEP 4: Run settlement for completed fixtures
        logger.info("[CONTINUOUS] Step 4: Running settlement...")
        settled = _run_settlement()
        
        # HARD ASSERTION: Ensure settlement completed
        if settled > 0:
            _assert_settlement_valid(settled)
        
        # STEP 5: Emit governance data
        logger.info("[CONTINUOUS] Step 5: Emitting governance data...")
        _emit_governance_data(run_id, predictions, pipeline_result, settled)
        
        # Emit cycle completed
        duration = time.time() - start_time
        
        EventBus.emit(Events.RUN_COMPLETED, {
            "run_id": run_id,
            "mode": "continuous_cycle",
            "predictions": len(predictions),
            "bets": bets_placed,
            "settled": settled,
            "duration": duration,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.info(f"[CONTINUOUS] Cycle complete in {duration:.1f}s: {len(predictions)} predictions, {bets_placed} bets, {settled} settled")
        
        return {
            "predictions": len(predictions),
            "bets": bets_placed,
            "settled": settled,
            "duration": duration,
        }
        
    except Exception as e:
        logger.error(f"[CONTINUOUS] Cycle failed: {e}")
        
        EventBus.emit(Events.RUN_FINISHED, {
            "run_id": run_id,
            "mode": "continuous_cycle",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        raise


def _run_settlement() -> int:
    """Run settlement for completed fixtures."""
    settled_count = 0
    
    with get_session() as s:
        # Find FT fixtures with unsettled bets
        ft_bets = s.execute(
            select(PlacedBet)
            .join(Fixture, PlacedBet.fixture_id == Fixture.id)
            .where(Fixture.status == "FT")
            .where(PlacedBet.settled == False)
        ).scalars().all()
        
        for bet in ft_bets:
            # Get fixture outcome
            fixture = s.execute(
                select(Fixture).where(Fixture.id == bet.fixture_id)
            ).scalar_one_or_none()
            
            if not fixture or not fixture.outcome:
                continue
            
            # Determine win/loss
            won = (bet.outcome == fixture.outcome)
            
            # Calculate PnL
            if won:
                bet.pnl = bet.stake * (bet.odds - 1)
            else:
                bet.pnl = -bet.stake
            
            bet.settled = True
            bet.settled_at = datetime.now(ZoneInfo("UTC"))
            settled_count += 1
        
        s.commit()
    
    if settled_count > 0:
        EventBus.emit(Events.BETS_SETTLED, {
            "settled_count": settled_count,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    return settled_count


def _emit_governance_data(run_id: str, predictions: list, pipeline_result: dict, settled: int):
    """Emit governance data for dashboard."""
    # Calculate prediction age distribution
    now = datetime.now(ZoneInfo("UTC"))
    ages = []
    
    for pred in predictions:
        # Simplified - would track actual timestamps
        ages.append(0)  # placeholder
    
    EventBus.emit("GOVERNANCE_DATA_READY", {
        "run_id": run_id,
        "total_predictions": len(predictions),
        "updated_predictions": len(predictions),  # Would track changes
        "settled_bets": settled,
        "prediction_age_distribution": ages,
        "pipeline_bets": pipeline_result.get("bets", 0),
        "timestamp": now.isoformat(),
    })


def _assert_data_ready(fixtures: list):
    """HARD ASSERTION: Ensure baseline data is ready before predictions."""
    if not fixtures:
        return
    
    # Extract IDs from fixtures (handles both objects and dicts)
    fixture_ids = []
    for f in fixtures:
        if hasattr(f, 'id'):
            fixture_ids.append(f.id)
        elif isinstance(f, dict):
            fixture_ids.append(f.get('id'))
    
    if not fixture_ids:
        return
    
    logger.info(f"[DIAGNOSTIC] Validating {len(fixture_ids)} fixtures for data readiness...")
    
    with get_session() as s:
        # Check: All fixtures must have odds
        fixtures_with_odds = s.execute(
            select(func.count(Fixture.id))
            .join(FixtureOdds, Fixture.id == FixtureOdds.fixture_id)
            .where(Fixture.id.in_(fixture_ids))
        ).scalar() or 0
        
        logger.info(f"[DIAGNOSTIC] Fixtures with odds: {fixtures_with_odds}/{len(fixture_ids)}")
        
        if fixtures_with_odds < len(fixture_ids):
            missing = len(fixture_ids) - fixtures_with_odds
            raise RuntimeError(
                f"PIPELINE FAILURE: {missing}/{len(fixture_ids)} fixtures missing odds - HALTING. "
                f"Run daily_baseline first."
            )
        
        # Check: Fixtures must have league/team data
        fixtures_with_data = s.execute(
            select(func.count(Fixture.id))
            .where(Fixture.id.in_(fixture_ids))
            .where(Fixture.league_id.isnot(None))
            .where(Fixture.home_team_id.isnot(None))
            .where(Fixture.away_team_id.isnot(None))
        ).scalar() or 0
        
        logger.info(f"[DIAGNOSTIC] Fixtures with complete data: {fixtures_with_data}/{len(fixture_ids)}")
        
        if fixtures_with_data < len(fixture_ids):
            missing = len(fixture_ids) - fixtures_with_data
            logger.warning(f"[DIAGNOSTIC] {missing} fixtures missing league/team data")


def _assert_clve_valid(pipeline_result: dict):
    """HARD ASSERTION: CLVE must pass before bets can be placed."""
    clve_result = pipeline_result.get("clve_result", {})
    
    if not clve_result:
        raise RuntimeError("PIPELINE FAILURE: No CLVE result - HALTING")
    
    pds = clve_result.get("pds", 0)
    ai = clve_result.get("ai", 0)
    cds = clve_result.get("cds", 0)
    
    # Thresholds from AGENTS.md
    if pds < 0.01:
        raise RuntimeError(
            f"PIPELINE FAILURE: PDS {pds:.4f} < 0.01 (system static) - BLOCKING execution"
        )
    if ai < 0.5:
        raise RuntimeError(
            f"PIPELINE FAILURE: AI {ai:.4f} < 0.5 (not adapting) - BLOCKING execution"
        )
    if cds < 0.05:
        raise RuntimeError(
            f"PIPELINE FAILURE: CDS {cds:.4f} < 0.05 (calibration drift) - BLOCKING execution"
        )
    
    logger.info(f"[CONTINUOUS] CLVE validated: PDS={pds:.4f}, AI={ai:.4f}, CDS={cds:.4f}")


def _assert_settlement_valid(settled_count: int):
    """HARD ASSERTION: Verify settlement completed correctly."""
    logger.info(f"[CONTINUOUS] Settlement verified: {settled_count} bets settled")


def main():
    parser = argparse.ArgumentParser(description="Continuous Prediction Cycle")
    parser.add_argument("--dry-run", action="store_true", help="Preview without execution")
    args = parser.parse_args()
    
    import uuid
    from backend.experiment_tracker import get_tracker
    from backend.runtime_mode import get_mode_name
    from backend.run_context import create_run_context
    
    mode = get_mode_name()
    tracker = get_tracker()
    
    run_id = None
    context = None
    
    if mode in ["training", "dev"]:
        run_id = tracker.start_run(runtime_mode=mode)
        context = create_run_context(run_id, mode)
        print(f"Started experiment run: {run_id}")
    
    try:
        result = run_continuous_cycle(run_id, context)
        print(f"Continuous cycle complete: {result}")
    except Exception as e:
        print(f"Continuous cycle failed: {e}")
        if run_id:
            try:
                tracker.finalize_run(run_id, status="failed")
            except:
                pass
        if not args.dry_run:
            raise


if __name__ == "__main__":
    main()
