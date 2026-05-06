import uuid
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class SystemSnapshot:
    model_versions: Dict[str, str] = field(default_factory=dict)
    calibrator_versions: Dict[str, str] = field(default_factory=dict)
    feature_pipeline_version: str = ""
    config_hash: str = ""
    runtime_mode: str = ""
    timestamp: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)


class ExperimentTracker:
    _instance = None
    _current_run_id: Optional[str] = None
    _snapshot: Optional[SystemSnapshot] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.db_path = "data/football.db"
    
    @staticmethod
    def generate_run_id() -> str:
        return str(uuid.uuid4())
    
    @staticmethod
    def generate_config_hash(config: Dict) -> str:
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]
    
    def capture_system_snapshot(self, runtime_mode: str, model_dir: str = "data") -> SystemSnapshot:
        snapshot = SystemSnapshot()
        snapshot.runtime_mode = runtime_mode
        snapshot.timestamp = datetime.now().isoformat()
        snapshot.feature_pipeline_version = "v1.0.0"

        # Primary: read version_label from DB (authoritative after migration 018).
        # Fallback: derive from pkl mtime at data/model_{market}.pkl.
        try:
            from src.models.model_registry import get_model_registry
            registry = get_model_registry()
            db_labels = registry.get_active_labels()
            model_versions = {m: db_labels.get(m, f"{m}_none") for m in ("h2h", "btts", "ou25", "ou15")}
        except Exception:
            logger.warning("Snapshot: DB version lookup failed, falling back to mtime")
            model_versions = {}
            for market in ("h2h", "btts", "ou25", "ou15"):
                model_path = Path(model_dir) / f"model_{market}.pkl"
                if model_path.exists():
                    model_versions[market] = f"{market}_{int(model_path.stat().st_mtime)}"
                else:
                    model_versions[market] = f"{market}_none"

        # Calibrator versions are embedded in the model pkl (same vXX_cYY label).
        calibrator_versions = {m: f"calib_{v}" for m, v in model_versions.items()}

        snapshot.model_versions = model_versions
        snapshot.calibrator_versions = calibrator_versions

        config = {"runtime_mode": runtime_mode, "model_versions": model_versions}
        snapshot.config_hash = self.generate_config_hash(config)

        self._snapshot = snapshot
        logger.info("System snapshot captured: %s model_versions=%s", snapshot.config_hash, model_versions)

        return snapshot
    
    def start_run(self, runtime_mode: str, model_dir: str = "data/models", record_in_db: bool = True) -> str:
        run_id = self.generate_run_id()
        self._current_run_id = run_id
        
        if self._snapshot is None:
            self.capture_system_snapshot(runtime_mode, model_dir)
        
        if record_in_db and runtime_mode != "live_eval":
            self._record_run_to_db(run_id, runtime_mode)
        
        logger.info(f"EXPERIMENT RUN STARTED: {run_id}")
        
        from src.alerts.event_bus import event_bus, Events
        event_bus.emit(Events.RUN_STARTED, {
            "run_id": run_id,
            "mode": runtime_mode,
            "summary": f"Run started: {runtime_mode}"
        })
        
        return run_id
    
    def _record_run_to_db(self, run_id: str, runtime_mode: str):
        from src.storage.db import get_session
        
        if self._snapshot is None:
            return
        
        try:
            with get_session() as s:
                s.execute(text("""
                    INSERT INTO experiment_runs (run_id, mode, start_timestamp, model_versions_json, calibrator_versions_json, feature_pipeline_version, config_hash)
                    VALUES (:run_id, :mode, :start_timestamp, :model_versions, :calibrator_versions, :feature_pipeline, :config_hash)
                """), {
                    "run_id": run_id, "mode": runtime_mode, "start_timestamp": datetime.now().isoformat(),
                    "model_versions": json.dumps(self._snapshot.model_versions),
                    "calibrator_versions": json.dumps(self._snapshot.calibrator_versions),
                    "feature_pipeline": self._snapshot.feature_pipeline_version,
                    "config_hash": self._snapshot.config_hash
                })
                s.commit()
        except Exception as e:
            logger.warning(f"Failed to record run to DB: {e}")
    
    def get_current_run_id(self) -> Optional[str]:
        return self._current_run_id
    
    def get_snapshot(self) -> Optional[SystemSnapshot]:
        return self._snapshot
    
    def record_predictions_made(self, count: int):
        from src.storage.db import get_session
        if self._current_run_id is None:
            return
        try:
            with get_session() as s:
                s.execute(text("UPDATE experiment_runs SET total_predictions = total_predictions + :count WHERE run_id = :run_id"),
                         {"count": count, "run_id": self._current_run_id})
                s.commit()
        except Exception as e:
            logger.warning(f"Failed to record predictions: {e}")
    
    def record_bets_placed(self, count: int):
        from src.storage.db import get_session
        if self._current_run_id is None:
            return
        try:
            with get_session() as s:
                s.execute(text("UPDATE experiment_runs SET total_bets = total_bets + :count WHERE run_id = :run_id"),
                         {"count": count, "run_id": self._current_run_id})
                s.commit()
        except Exception as e:
            logger.warning(f"Failed to record bets: {e}")
    
    def finalize_run(self, bankroll_snapshot: Optional[float] = None, final_metrics: Optional[Dict] = None):
        from src.storage.db import get_session
        if self._current_run_id is None:
            return
        try:
            with get_session() as s:
                s.execute(text("""
                    UPDATE experiment_runs SET end_timestamp = :end_timestamp, bankroll_snapshot = :bankroll,
                           final_metrics_json = :metrics, status = 'completed' WHERE run_id = :run_id
                """), {"end_timestamp": datetime.now().isoformat(), "bankroll": bankroll_snapshot,
                       "metrics": json.dumps(final_metrics) if final_metrics else None, "run_id": self._current_run_id})
                s.commit()
        except Exception as e:
            logger.warning(f"Failed to finalize run: {e}")
        finished_run_id = self._current_run_id
        self._current_run_id = None

        from src.alerts.event_bus import event_bus, Events
        event_bus.emit(Events.RUN_FINISHED, {
            "run_id": finished_run_id,
            "bankroll_snapshot": bankroll_snapshot,
            "final_metrics": final_metrics,
            "summary": f"Run completed: {finished_run_id}, bankroll: {bankroll_snapshot}"
        })
    
    def get_run_id_for_prediction(self) -> str:
        if self._current_run_id is None:
            self.start_run("dev", record_in_db=False)
        return self._current_run_id


def compute_run_metrics(run_id: str) -> Dict:
    from src.storage.db import get_session

    metrics = {"run_id": run_id, "total_predictions": 0, "total_bets": 0, "settled_predictions": 0, "winning_predictions": 0,
               "market_breakdown": {}, "avg_ev": 0.0, "avg_calibrated_prob": 0.0, "brier_score": 0.0, "ece": 0.0}

    try:
        with get_session() as s:
            # Also pull the stored counters from experiment_runs as an authoritative fallback
            # for prediction/bet counts when the child rows carry a different run_id.
            er = s.execute(text(
                "SELECT total_predictions, total_bets FROM experiment_runs WHERE run_id = :run_id"
            ), {"run_id": run_id}).fetchone()
            stored_pred_count = er.total_predictions or 0 if er else 0
            stored_bet_count  = er.total_bets or 0      if er else 0

            pred_results = s.execute(text("""
                SELECT COUNT(*) as total, SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
                       SUM(CASE WHEN settled = 1 AND won = 1 THEN 1 ELSE 0 END) as wins,
                       AVG(ev) as avg_ev, AVG(calibrated_prob) as avg_prob
                FROM prediction_records WHERE run_id = :run_id
            """), {"run_id": run_id}).fetchone()

            if pred_results:
                # Use whichever is larger: directly linked rows or the stored counter.
                # The stored counter is what was incremented at run time; linked rows
                # may be fewer because predictions are deduplicated by fixture+market.
                direct_count = pred_results.total or 0
                metrics["total_predictions"] = max(direct_count, stored_pred_count)
                metrics["settled_predictions"] = pred_results.settled or 0
                metrics["winning_predictions"] = pred_results.wins or 0
                metrics["avg_ev"] = pred_results.avg_ev or 0.0
                metrics["avg_calibrated_prob"] = pred_results.avg_prob or 0.0

            bet_results = s.execute(text("""
                SELECT COUNT(*) as total, SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
                       SUM(CASE WHEN settled = 1 AND won = 1 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN settled = 1 THEN pnl ELSE 0 END) as total_pnl
                FROM placed_bets WHERE run_id = :run_id
            """), {"run_id": run_id}).fetchone()

            if bet_results:
                direct_count = bet_results.total or 0
                metrics["total_bets"] = max(direct_count, stored_bet_count)
                metrics["settled_bets"] = bet_results.settled or 0
                metrics["winning_bets"] = bet_results.wins or 0
                metrics["total_pnl"] = bet_results.total_pnl or 0.0
            
            if metrics["settled_predictions"] > 0:
                probs = s.execute(text("SELECT calibrated_prob, won FROM prediction_records WHERE run_id = :run_id AND settled = 1 AND calibrated_prob IS NOT NULL"),
                                {"run_id": run_id}).fetchall()
                if probs:
                    prob_arrays = np.array([(p.calibrated_prob, int(p.won)) for p in probs])
                    if len(prob_arrays) > 0:
                        preds = prob_arrays[:, 0]
                        outcomes = prob_arrays[:, 1]
                        metrics["brier_score"] = float(np.mean((preds - outcomes) ** 2))
                        bins = np.linspace(0, 1, 11)
                        ece = 0.0
                        for i in range(len(bins) - 1):
                            mask = (preds >= bins[i]) & (preds < bins[i+1])
                            if np.sum(mask) > 0:
                                bin_acc = np.mean(outcomes[mask])
                                bin_conf = np.mean(preds[mask])
                                ece += (np.sum(mask) / len(preds)) * abs(bin_acc - bin_conf)
                        metrics["ece"] = float(ece)
    except Exception as e:
        logger.warning(f"Failed to compute run metrics: {e}")
    
    return metrics


def get_experiment_runs(limit: int = 10) -> List[Dict]:
    from src.storage.db import get_session
    
    runs = []
    try:
        with get_session() as s:
            results = s.execute(text("""
                SELECT run_id, mode, start_timestamp, end_timestamp, model_versions_json, calibrator_versions_json,
                       total_predictions, total_bets, bankroll_snapshot, config_hash, status
                FROM experiment_runs ORDER BY start_timestamp DESC LIMIT :limit
            """), {"limit": limit}).fetchall()
            
            for row in results:
                runs.append({
                    "run_id": row.run_id, "mode": row.mode, "start_timestamp": row.start_timestamp,
                    "end_timestamp": row.end_timestamp, "model_versions": json.loads(row.model_versions_json) if row.model_versions_json else {},
                    "calibrator_versions": json.loads(row.calibrator_versions_json) if row.calibrator_versions_json else {},
                    "total_predictions": row.total_predictions, "total_bets": row.total_bets,
                    "bankroll_snapshot": row.bankroll_snapshot, "config_hash": row.config_hash, "status": row.status
                })
    except Exception as e:
        logger.warning(f"Failed to get experiment runs: {e}")
    
    return runs


def get_tracker() -> ExperimentTracker:
    return ExperimentTracker()


def compute_layer_attribution_aggregation(run_id: str) -> Dict:
    """Aggregate layer attribution scores by layer type for a run."""
    from src.storage.db import get_session
    
    result = {
        'feature_engineering': 0.0,
        'odds_movement': 0.0,
        'injury_availability': 0.0,
        'elo_ratings': 0.0,
        'form_derivation': 0.0,
        'lineup_detection': 0.0,
        'market_specific': 0.0,
        'calibration': 0.0,
        'total_contributions': 0.0
    }
    
    try:
        with get_session() as sess:
            from src.storage.models import PredictionAttribution
            rows = sess.query(PredictionAttribution).filter(
                PredictionAttribution.run_id == run_id
            ).all()
            
            if not rows:
                return result
            
            layer_scores = {}
            for row in rows:
                layer = row.layer_name or 'unknown'
                score = row.attribution_score or 0.0
                layer_scores[layer] = layer_scores.get(layer, 0.0) + score
            
            for layer, score in layer_scores.items():
                if layer in result:
                    result[layer] = round(score, 4)
            
            result['total_contributions'] = round(sum(layer_scores.values()), 4)
    except Exception as e:
        logger.warning(f"Failed to compute layer attribution: {e}")
    
    return result


def compute_attribution_by_market(run_id: str) -> Dict:
    """Compute prediction breakdown by market for a run."""
    from src.storage.db import get_session

    _empty = lambda: {
        'total_predictions': 0,
        'settled': 0,
        'win_rate': 0.0,
        'avg_ev': 0.0,
        'total_ev': 0.0,
        'layer_deltas': {},
    }
    result = {m: _empty() for m in ('h2h', 'ou25', 'ou15', 'btts')}

    _query = """
        SELECT
            pr.market,
            COUNT(*)                                                          AS total_predictions,
            SUM(CASE WHEN pr.settled = 1 THEN 1 ELSE 0 END)                 AS settled,
            AVG(CASE WHEN pr.settled = 1 AND pr.won IS NOT NULL
                     THEN CAST(pr.won AS FLOAT) ELSE NULL END)               AS win_rate,
            AVG(pr.ev)                                                        AS avg_ev,
            SUM(pr.ev)                                                        AS total_ev,
            AVG(pa.calibration_delta)                                         AS calibration_delta,
            AVG(pa.risk_delta)                                                AS risk_delta
        FROM prediction_records pr
        LEFT JOIN prediction_attribution pa ON pa.prediction_id = pr.id
        WHERE {where}
        GROUP BY pr.market
    """

    try:
        with get_session() as s:
            rows = s.execute(
                text(_query.format(where="pr.run_id = :run_id")),
                {"run_id": run_id},
            ).fetchall()

            if not rows:
                rows = s.execute(
                    text(_query.format(where="pr.is_legacy = 0")),
                ).fetchall()

            for row in rows:
                market = row.market
                if market in result:
                    result[market]['total_predictions'] = row.total_predictions or 0
                    result[market]['settled']           = row.settled or 0
                    result[market]['win_rate']          = round(row.win_rate or 0.0, 4)
                    result[market]['avg_ev']            = round(row.avg_ev or 0.0, 4)
                    result[market]['total_ev']          = round(row.total_ev or 0.0, 4)
                    result[market]['layer_deltas']      = {
                        'calibration': round(row.calibration_delta or 0.0, 4),
                        'risk':        round(row.risk_delta or 0.0, 4),
                    }

    except Exception:
        logger.exception("Failed to compute attribution by market for run %s", run_id)

    return result