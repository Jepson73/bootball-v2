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
from src.calibration.market_blend import blend_with_market

_cal_engine = LeagueCalibrationEngine()

logger = logging.getLogger(__name__)

# Versioning tuple constants — update these when any of the four layers changes.
# These tag every prediction_records row so provenance is always recoverable.
FEATURE_PIPELINE_VERSION = "v1.0.0"   # standings-only (9 features); bump to v2.0.0 with Wave 1
BLEND_VERSION            = "v1.0"     # blend_with_market(MODEL_WEIGHT=0.35, Shin); None = no blend


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

        if fixtures is None:
            fixtures = self._fetch_upcoming_fixtures()

        if not fixtures:
            raise RuntimeError("PIPELINE FAILURE: No fixtures available for prediction pipeline")

        return self.generate_with_fixture_data(fixtures)
    
    def _fetch_upcoming_fixtures(self) -> list:
        """Fetch all upcoming NS fixtures — no odds filter.

        Predictions are generated for every fixture regardless of odds availability.
        EV/Kelly are None/0 (preliminary=True) when odds are absent; that is the
        value layer's concern, not the prediction engine's.
        """
        from sqlalchemy import select

        class _Stub:
            __slots__ = ("id", "home_team_id", "away_team_id", "league_id", "date", "status")
            def __init__(self, f):
                self.id = f.id
                self.home_team_id = f.home_team_id
                self.away_team_id = f.away_team_id
                self.league_id = f.league_id
                self.date = f.date
                self.status = f.status

        with get_session() as s:
            rows = s.execute(
                select(Fixture)
                .where(Fixture.status == "NS")
                .where(Fixture.date >= datetime.utcnow())
                .order_by(Fixture.date.asc())
            ).scalars().all()
            return [_Stub(f) for f in rows]
    
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

    _MARKET_OUTCOME_FIELDS = {
        "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
        "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
        "ou25": {"Over": "odd_over", "Under": "odd_under"},
        "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
    }

    def _get_market_odds_set(self, fixture_id: int, market: str) -> Optional[dict[str, float]]:
        """Get decimal odds for ALL mutually-exclusive outcomes of a market.

        Needed to de-vig via Shin's method (src/betting/shin.py), which
        requires the full set of odds, not a single outcome's price.
        Returns None if any outcome's odds are missing.
        """
        from sqlalchemy import select

        fields = self._MARKET_OUTCOME_FIELDS.get(market)
        if not fields:
            return None

        with get_session() as s:
            rows = s.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_id)
            ).scalars().all()

        if not rows:
            return None

        result = {}
        for label, field in fields.items():
            value = max([getattr(r, field) for r in rows if getattr(r, field)], default=None)
            if value is None:
                return None
            result[label] = value
        return result

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
                    p_blended = p_final
                    p_market = None
                    if has_odds:
                        implied_prob = 1.0 / odds

                        # Shrink toward the de-vigged market-implied probability before
                        # computing EV/Kelly — the market was shown to be far closer to
                        # the true outcome rate than our "calibrated" probability across
                        # every market. p_final is preserved separately for comparison.
                        market_odds = self._get_market_odds_set(fixture_id, normalized_market)
                        if market_odds:
                            p_blended, p_market = blend_with_market(p_final, market_odds, normalized_outcome)

                        ev = p_blended * odds - 1  # EV uses market-blended probability
                        b = odds - 1
                        q = 1 - p_blended
                        kelly = max(0, (b * p_blended - q) / b) * 0.25 if b > 0 else 0
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
                        "calibrated_prob": p_final,     # VCL output — kept for comparison
                        "calibration_version": cal_version,
                        "market_prob": p_market,        # de-vigged (Shin) market-implied prob
                        "blended_prob": p_blended,      # final — used for EV/Kelly/betting
                        "implied_prob": implied_prob if has_odds else None,
                        "predicted_probs": model_probs,
                        "ev": ev,
                        "kelly": kelly,
                        "preliminary": not has_odds,
                        "timestamp": datetime.utcnow().isoformat(),
                        # Versioning tuple — identifies exact code stack that produced this prediction
                        "feature_pipeline_version": FEATURE_PIPELINE_VERSION,
                        "blend_version": BLEND_VERSION if (has_odds and p_market is not None) else None,
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

                record.market_prob = pred.get("market_prob")
                record.blended_prob = pred.get("blended_prob")

                # Versioning tuple — written explicitly so no row relies on column defaults
                if pred.get("feature_pipeline_version"):
                    record.feature_pipeline_version = pred["feature_pipeline_version"]
                if pred.get("blend_version") is not None or "blend_version" in pred:
                    record.blend_version = pred.get("blend_version")

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

    # ── Track-A Evaluation (proper scoring rules) ─────────────────────────────

    @staticmethod
    def evaluate_track_a(market: str, settled_records: list) -> dict:
        """Compute Track-A proper scoring metrics on settled predictions.

        Each record must be a dict or ORM object with:
          - our_prob:          model probability of the PREDICTED outcome
          - predicted_outcome: model's pick ("Home", "Draw", "Away", "Over", "Under", "Yes", "No")
          - actual_outcome:    what actually happened (same label space as predicted_outcome)
          For h2h, also provide `prob_home` (float); our_prob alone cannot reconstruct
          P(Home) after the 3-class output collapses to a scalar.

        For binary markets (btts, ou25, ou15):
          P(reference) = our_prob if predicted==reference else 1 - our_prob
          actual_binary = 1 if actual_outcome==reference else 0

        For h2h:
          P(Home) must be in `prob_home`; records without it are counted in
          `skipped_no_prob_home` and excluded from metrics.

        NOTE: production uses LGBMClassifier on standings features (AUC ~0.56–0.58).
        Phase 10 DC+xG achieved 0.71; Track-A scores here reflect the weaker
        production baseline, not the research model.
        """
        import math

        def _get(r, key, default=None):
            return r.get(key, default) if isinstance(r, dict) else getattr(r, key, default)

        BINARY_REFERENCE = {"btts": "Yes", "ou25": "Over", "ou15": "Over"}

        probs: list[float] = []
        actuals: list[int] = []
        skipped_no_outcome = 0
        skipped_no_prob_home = 0

        for r in settled_records:
            our_prob = _get(r, "our_prob")
            predicted = str(_get(r, "predicted_outcome") or "")
            actual_outcome = str(_get(r, "actual_outcome") or "")

            if our_prob is None or not predicted or not actual_outcome:
                skipped_no_outcome += 1
                continue

            if market in BINARY_REFERENCE:
                ref = BINARY_REFERENCE[market]
                p_ref = float(our_prob) if predicted == ref else 1.0 - float(our_prob)
                actual_bin = 1 if actual_outcome == ref else 0
            elif market == "h2h":
                prob_home = _get(r, "prob_home")
                if prob_home is None:
                    skipped_no_prob_home += 1
                    continue
                p_ref = float(prob_home)
                actual_bin = 1 if actual_outcome == "Home" else 0
            else:
                skipped_no_outcome += 1
                continue

            probs.append(p_ref)
            actuals.append(actual_bin)

        n = len(probs)
        result: dict = {
            "n": n,
            "market": market,
            "log_loss": None,
            "brier": None,
            "auc": None,
            "base_rate": None,
        }
        if skipped_no_outcome:
            result["skipped_no_outcome"] = skipped_no_outcome
        if skipped_no_prob_home:
            result["skipped_no_prob_home"] = skipped_no_prob_home

        if n == 0:
            return result

        eps = 1e-9
        result["log_loss"] = round(
            -sum(
                a * math.log(max(p, eps)) + (1 - a) * math.log(max(1 - p, eps))
                for p, a in zip(probs, actuals)
            ) / n,
            5,
        )
        result["brier"] = round(sum((p - a) ** 2 for p, a in zip(probs, actuals)) / n, 5)

        pos = [p for p, a in zip(probs, actuals) if a == 1]
        neg = [p for p, a in zip(probs, actuals) if a == 0]
        if pos and neg:
            u = sum(1 if pp > pn else 0.5 if pp == pn else 0 for pp in pos for pn in neg)
            result["auc"] = round(u / (len(pos) * len(neg)), 5)

        result["base_rate"] = round(sum(actuals) / n, 4)
        return result


# Global service
_service: Optional[UnifiedPredictionService] = None


def get_unified_prediction_service() -> UnifiedPredictionService:
    """Get global unified prediction service."""
    global _service
    if _service is None:
        _service = UnifiedPredictionService()
    return _service
