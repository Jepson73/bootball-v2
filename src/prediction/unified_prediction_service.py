"""
UnifiedPredictionService - Single source of truth for all predictions.

This service replaces split between legacy scripts and AgentCoordinator.
All predictions in the system MUST flow through this service.

Usage:
    predictions = UnifiedPredictionService().generate(fixtures)
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events
from src.prediction.market_normalizer import normalize_market, normalize_market_pick
from src.storage.db import get_session
from src.storage.models import Fixture, FixtureOdds, PredictionRecord
from src.betting.prediction import get_model_prediction
from src.calibration.league_calibration_engine import LeagueCalibrationEngine

_cal_engine = LeagueCalibrationEngine()

logger = logging.getLogger(__name__)


class UnifiedPredictionService:
    """
    Single source of truth for all predictions in the system.
    
    This service:
    1. Uses existing working model pipeline
    2. Returns standardized prediction format
    3. Enforces pipeline validation
    4. Emits prediction events
    """
    
    def __init__(self):
        self._system_mode = "PORTFOLIO_PRIMARY"  # Default enforcement
        logger.info("[PREDICTION] UnifiedPredictionService initialized - SINGLE SOURCE OF TRUTH")
    
    def generate(self, fixtures: list = None) -> list[dict]:
        """
        Generate predictions for fixtures.
        
        Args:
            fixtures: Optional list of fixture objects/IDs. If None, fetches from DB.
            
        Returns:
            List of prediction dicts with standardized format:
            {
                "fixture_id": int,
                "market": str,
                "outcome": str,
                "odds": float,
                "our_prob": float,
                "ev": float,
                "kelly": float,
            }
        """
        logger.info("[PREDICTION] Generating predictions via unified service")
        
        # Fetch fixtures if not provided
        if fixtures is None:
            fixtures = self._fetch_upcoming_fixtures()
        
        if not fixtures:
            raise RuntimeError("PIPELINE FAILURE: No fixtures available for prediction pipeline")
        
        predictions = []
        
        for fixture in fixtures:
            fixture_id = fixture.id if hasattr(fixture, 'id') else fixture
            
            # Generate predictions for each market
            market_predictions = self._generate_for_fixture(fixture_id)
            predictions.extend(market_predictions)
        
        if not predictions:
            raise RuntimeError("PIPELINE FAILURE: No predictions generated - pipeline broken")
        
        # Emit predictions ready event
        event_bus.emit(Events.PREDICTIONS_GENERATED, {
            "count": len(predictions),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.info(f"[PREDICTION] Generated {len(predictions)} predictions")
        
        return predictions
    
    def _fetch_upcoming_fixtures(self) -> list:
        """Fetch upcoming fixtures that have odds."""
        with get_session() as s:
            fixtures = s.execute(
                Fixture.query.filter(
                    Fixture.status == "NS",
                    Fixture.date >= datetime.utcnow()
                ).join(FixtureOdds).limit(50).all()
            ).scalars().all()
        
        return fixtures
    
    def _generate_for_fixture(self, fixture_id: int) -> list[dict]:
        """Generate predictions for a single fixture."""
        predictions = []
        
        markets = ["h2h", "btts", "ou25", "ou15"]
        
        for market in markets:
            try:
                normalized_market = normalize_market(market)
                
                model_probs = get_model_prediction(
                    market=normalized_market,
                    home_team_id=None,
                    away_team_id=None
                )
                
                if not model_probs:
                    continue
                
                best_outcome = max(model_probs.items(), key=lambda x: x[1])
                raw_outcome = best_outcome[0]
                our_prob = best_outcome[1]
                
                normalized_outcome = normalize_market_pick(normalized_market, raw_outcome)
                
                odds, odds_snapshot = self._get_odds_for_market(fixture_id, normalized_market)
                
                if not odds or odds < 1.0:
                    continue
                
                ev = (our_prob * odds) - (1 - our_prob)
                
                b = odds - 1
                q = 1 - our_prob
                kelly = max(0, (b * our_prob - q) / b) if b > 0 else 0
                kelly = kelly * 0.25
                
                prediction_id = str(uuid.uuid4())
                
                predictions.append({
                    "prediction_id": prediction_id,
                    "fixture_id": fixture_id,
                    "market": normalized_market,
                    "outcome": normalized_outcome,
                    "raw_outcome": raw_outcome,
                    "odds": odds,
                    "odds_snapshot": odds_snapshot,
                    "our_prob": our_prob,
                    "predicted_probs": model_probs,
                    "ev": ev,
                    "kelly": kelly,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                
            except Exception as e:
                logger.warning(f"[PREDICTION] Failed for fixture {fixture_id}, market {market}: {e}")
                continue
        
        return predictions
    
    def _get_odds_for_market(self, fixture_id: int, market: str, outcome: str = None) -> tuple[Optional[float], Optional[str]]:
        """Get odds for a market and specific outcome."""
        from sqlalchemy import select
        
        with get_session() as s:
            rows = s.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_id)
            ).scalars().all()
            
            if not rows:
                return None, None
            
            odds_value = None
            snapshot = {}
            
            if market == "h2h":
                if outcome == "1":
                    odds_value = max([r.odd_home for r in rows if r.odd_home], default=None)
                    if odds_value:
                        snapshot = {"odd_home": odds_value}
                elif outcome == "2":
                    odds_value = max([r.odd_away for r in rows if r.odd_away], default=None)
                    if odds_value:
                        snapshot = {"odd_away": odds_value}
                elif outcome in ("X", "D", "draw"):
                    odds_value = max([r.odd_draw for r in rows if r.odd_draw], default=None)
                    if odds_value:
                        snapshot = {"odd_draw": odds_value}
                else:
                    odds_value = max([r.odd_home for r in rows if r.odd_home], default=None)
            
            elif market == "btts":
                if outcome in ("Yes", "yes", "BTTS_Yes"):
                    odds_value = max([r.odd_btts_yes for r in rows if r.odd_btts_yes], default=None)
                else:
                    odds_value = max([r.odd_btts_no for r in rows if r.odd_btts_no], default=None)
            
            elif market == "ou25":
                if outcome in ("Over", "over", "o25"):
                    odds_value = max([r.odd_over for r in rows if r.odd_over], default=None)
                else:
                    odds_value = max([r.odd_under for r in rows if r.odd_under], default=None)
            
            elif market == "ou15":
                if outcome in ("Over", "over", "o15"):
                    odds_value = max([r.odd_over15 for r in rows if r.odd_over15], default=None)
                else:
                    odds_value = max([r.odd_under15 for r in rows if r.odd_under15], default=None)
            
            if odds_value:
                snapshot = {"odds": odds_value, "bookmaker": rows[0].bookmaker if rows else None}
            
            odds_snapshot = json.dumps(snapshot) if snapshot else None
            
            return odds_value, odds_snapshot
    
    def generate_with_fixture_data(self, fixture_objects: list) -> list[dict]:
        """
        Generate predictions with full fixture objects.
        
        Args:
            fixture_objects: List of Fixture objects with home/away team info
            
        Returns:
            List of prediction dicts
        """
        logger.info(f"[PREDICTION] Generating predictions for {len(fixture_objects)} fixtures")
        
        predictions = []
        
        for fixture in fixture_objects:
            fixture_id = fixture.id
            home_id = fixture.home_team_id
            away_id = fixture.away_team_id
            
            markets = ["h2h", "btts", "ou25", "ou15"]
            
            for market in markets:
                try:
                    normalized_market = normalize_market(market)
                    
                    model_probs = get_model_prediction(
                        market=normalized_market,
                        home_team_id=home_id,
                        away_team_id=away_id
                    )
                    
                    if not model_probs:
                        continue
                    
                    best_outcome = max(model_probs.items(), key=lambda x: x[1])
                    raw_outcome = best_outcome[0]
                    our_prob = best_outcome[1]  # raw Vxx — stored for C-calibration training

                    normalized_outcome = normalize_market_pick(normalized_market, raw_outcome)

                    # Apply VCL calibration: Lzzzz → L0000 → raw fallback
                    league_id = getattr(fixture, 'league_id', None)  # None triggers L0000 fallback
                    p_final, cal_version = _cal_engine.apply(normalized_market, league_id, our_prob)

                    odds, odds_snapshot = self._get_odds_for_market(fixture_id, normalized_market, normalized_outcome)

                    # Preliminary predictions are allowed when odds are unavailable.
                    # ev and kelly are None/0 so the portfolio engine naturally skips them.
                    has_odds = odds is not None and odds >= 1.0
                    if has_odds:
                        implied_prob = 1.0 / odds
                        ev = p_final * odds - 1  # EV uses calibrated probability
                        b = odds - 1
                        q = 1 - p_final
                        kelly = max(0, (b * p_final - q) / b) * 0.25 if b > 0 else 0
                    else:
                        odds = None
                        odds_snapshot = None
                        ev = None
                        kelly = 0.0

                    prediction_id = str(uuid.uuid4())

                    predictions.append({
                        "prediction_id": prediction_id,
                        "fixture_id": fixture_id,
                        "league_id": league_id,
                        "home_team_id": home_id,
                        "away_team_id": away_id,
                        "market": normalized_market,
                        "outcome": normalized_outcome,
                        "raw_outcome": raw_outcome,
                        "odds": odds,
                        "odds_snapshot": odds_snapshot,
                        "our_prob": our_prob,           # raw Vxx preserved for C-training
                        "calibrated_prob": p_final,     # VCL final — used for EV/Kelly/betting
                        "calibration_version": cal_version,
                        "implied_prob": implied_prob if has_odds else None,
                        "predicted_probs": model_probs,
                        "ev": ev,
                        "kelly": kelly,
                        "preliminary": not has_odds,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                    
                except Exception as e:
                    logger.warning(f"[PREDICTION] Failed {fixture_id}/{market}: {e}")
        
        if not predictions:
            raise RuntimeError("PIPELINE FAILURE: No predictions generated")
        
        # Emit event
        event_bus.emit(Events.PREDICTIONS_GENERATED, {
            "count": len(predictions),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.info(f"[PREDICTION] Generated {len(predictions)} predictions")
        
        return predictions
    
    def _pred_to_dict(self, pred):
        """Convert PredictionPacket to dict for compatibility."""
        if isinstance(pred, dict):
            return pred
        # Convert PredictionPacket dataclass to dict
        return {
            "prediction_id": pred.prediction_id,
            "fixture_id": pred.fixture_id,
            "market": pred.market,
            "model_version": pred.model_version,
            "calibration_version": pred.calibration_version,
            "system_version": pred.system_version,
            "predicted_probs": pred.predicted_probs,
            "odds": pred.odds,
            "outcome": pred.outcome,
            "our_prob": pred.our_prob,
            "calibrated_prob": pred.calibrated_prob,
            "ev": pred.ev,
            "timestamp": pred.timestamp,
        }
    
    def save_predictions(self, predictions: list, run_id: str = None) -> list[int]:
        """
        Save predictions to database in a single batched transaction.

        Rules:
        - New fixture/market pair: always INSERT.
        - Existing preliminary (no odds) + incoming preliminary: SKIP (nothing changed).
        - Existing preliminary + incoming has odds: UPDATE (odds arrived).
        - Existing has odds + incoming has odds: UPDATE with fresh odds/EV.
        - Existing has odds + incoming is preliminary: SKIP (never downgrade).
        """
        from sqlalchemy import select, func, tuple_
        from src.storage.models import PredictionRecord
        from src.storage.db import get_session
        from src.calibration.league_calibration_engine import LeagueCalibrationEngine

        _cal_engine = LeagueCalibrationEngine()

        preds = [self._pred_to_dict(p) for p in predictions]

        for pred in preds:
            if not pred.get("prediction_id"):
                raise RuntimeError("LEGACY PREDICTION DETECTED: All predictions must have prediction_id")

        # Collect all fixture+market keys in this batch
        keys = [(p["fixture_id"], p["market"]) for p in preds]

        saved_ids = []
        inserted = updated = skipped = 0

        with get_session() as s:
            # Cache active model version IDs by market
            from src.storage.models import ModelVersion
            active_versions = s.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)
            ).scalars().all()
            model_version_cache = {mv.market: mv.id for mv in active_versions}

            # Single query to load all existing records for these fixtures
            existing_records = s.execute(
                select(PredictionRecord).where(
                    tuple_(PredictionRecord.fixture_id, PredictionRecord.market).in_(keys)
                )
            ).scalars().all()

            existing_map = {(r.fixture_id, r.market): r for r in existing_records}

            for pred in preds:
                fixture_id = pred["fixture_id"]
                market = pred["market"]
                incoming_odds = pred.get("odds")
                key = (fixture_id, market)

                record = existing_map.get(key)

                if record is None:
                    # New prediction
                    record = PredictionRecord()
                    s.add(record)
                    inserted += 1
                else:
                    existing_has_odds = record.odds_decimal is not None
                    incoming_has_odds = incoming_odds is not None

                    if not existing_has_odds and not incoming_has_odds:
                        # Both preliminary — nothing changed, skip the write
                        saved_ids.append(record.id)
                        skipped += 1
                        continue

                    if existing_has_odds and not incoming_has_odds:
                        # Would downgrade existing odds record to preliminary — skip
                        saved_ids.append(record.id)
                        skipped += 1
                        continue

                    updated += 1

                record.prediction_id = pred.get("prediction_id")
                record.fixture_id = fixture_id
                record.market = market
                if record.model_version_id is None:
                    record.model_version_id = model_version_cache.get(market)
                record.predicted_outcome = pred.get("outcome")
                record.raw_outcome = pred.get("raw_outcome")
                record.our_prob = pred.get("our_prob")
                record.implied_prob = pred.get("implied_prob")
                record.odds_decimal = incoming_odds
                record.odds_snapshot = pred.get("odds_snapshot")
                if record.odds_snapshot:
                    try:
                        import json as _json
                        _snap = _json.loads(record.odds_snapshot)
                        record.bookmaker = _snap.get("bookmaker")
                    except Exception:
                        pass
                record.ev = pred.get("ev")
                record.run_id = run_id
                record.is_legacy = False

                # Use calibrated_prob from prediction dict if already applied upstream,
                # otherwise apply here as fallback (e.g. legacy generate() path)
                if pred.get("calibrated_prob") is not None:
                    record.calibrated_prob = pred["calibrated_prob"]
                    record.calibration_version_id = pred.get("calibration_version")
                elif record.our_prob is not None:
                    fix = s.get(Fixture, fixture_id)
                    if fix and fix.league_id:
                        p_cal, cal_ver = _cal_engine.apply(market, fix.league_id, record.our_prob)
                        if cal_ver:
                            record.calibrated_prob = p_cal
                            record.calibration_version_id = cal_ver

                if pred.get("timestamp"):
                    record.timestamp = datetime.fromisoformat(pred["timestamp"])

            s.commit()

            # Collect IDs after commit (needed for inserted rows)
            for pred in preds:
                key = (pred["fixture_id"], pred["market"])
                if key in existing_map:
                    saved_ids.append(existing_map[key].id)
                # inserted rows are already flushed after commit; re-query if needed

            # Verify
            verified_count = s.execute(
                select(func.count(PredictionRecord.id)).where(PredictionRecord.is_legacy == 0)
            ).scalar() or 0

        logger.info(
            f"SAVED_PREDICTIONS: inserted={inserted} updated={updated} skipped={skipped} "
            f"total_in_db={verified_count}"
        )

        assert verified_count > 0, f"PREDICTIONS NOT PERSISTED: verified={verified_count}"
        return saved_ids


# Global service
_service: Optional[UnifiedPredictionService] = None


def get_unified_prediction_service() -> UnifiedPredictionService:
    """Get global unified prediction service."""
    global _service
    if _service is None:
        _service = UnifiedPredictionService()
    return _service
