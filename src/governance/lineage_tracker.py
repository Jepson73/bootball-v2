#!/usr/bin/env python3
"""
src/governance/lineage_tracker.py

Tracks lineage of decisions through the pipeline for full replay debugging.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.governance.system_versioning import RunLineage, VersionManager

logger = logging.getLogger(__name__)


class LineageTracker:
    """Tracks and persists lineage for each run."""
    
    def __init__(self, lineage_dir: str = "data/lineage"):
        self.lineage_dir = Path(lineage_dir)
        self.lineage_dir.mkdir(parents=True, exist_ok=True)
        self._current_lineage: Optional[RunLineage] = None
    
    def start_run(self, run_id: str) -> RunLineage:
        """Start tracking a new run."""
        self._current_lineage = VersionManager.create_lineage(run_id)
        logger.info(f"[LINEAGE] Started tracking run: {run_id}")
        return self._current_lineage
    
    def record_prediction(self, prediction_id: str, fixture_id: int, market: str, 
                        model_version: str = None, calibration_version: str = None):
        """Record a prediction in the lineage."""
        if self._current_lineage:
            self._current_lineage.add_prediction_id(prediction_id)
            if model_version:
                self._current_lineage.model_version = model_version
            if calibration_version:
                self._current_lineage.calibration_version = calibration_version
            logger.debug(f"[LINEAGE] Recorded prediction: {prediction_id}")
    
    def record_portfolio(self, portfolio_id: str):
        """Record portfolio generation."""
        if self._current_lineage:
            self._current_lineage.set_portfolio(portfolio_id)
            logger.debug(f"[LINEAGE] Recorded portfolio: {portfolio_id}")
    
    def record_risk(self, risk_id: str, decision_version: str):
        """Record risk evaluation."""
        if self._current_lineage:
            self._current_lineage.set_risk(risk_id, decision_version)
            logger.debug(f"[LINEAGE] Recorded risk: {risk_id}")
    
    def record_policy(self, policy_id: str, policy_version: str):
        """Record policy decision."""
        if self._current_lineage:
            self._current_lineage.set_policy(policy_id, policy_version)
            logger.debug(f"[LINEAGE] Recorded policy: {policy_id}")
    
    def record_execution(self, execution_id: str):
        """Record execution."""
        if self._current_lineage:
            self._current_lineage.set_execution(execution_id)
            logger.debug(f"[LINEAGE] Recorded execution: {execution_id}")
    
    def set_run_metrics(self, prediction_count: int = 0, bet_count: int = 0, health_score: float = 0.0):
        """Set run metrics for finalization."""
        if self._current_lineage:
            self._current_lineage.prediction_count = prediction_count
            self._current_lineage.bet_count = bet_count
            self._current_lineage.health_score = health_score
            logger.debug(f"[LINEAGE] Set metrics: predictions={prediction_count}, bets={bet_count}, health={health_score}")
    
    def set_experiment(self, experiment: bool, strategy_variant: str = "baseline"):
        """Mark run as experiment with strategy variant."""
        if self._current_lineage:
            self._current_lineage.experiment = experiment
            self._current_lineage.strategy_variant = strategy_variant
            logger.debug(f"[LINEAGE] Set experiment: {experiment}, variant: {strategy_variant}")
    
    def complete_run(self, status: str = "COMPLETE"):
        """Complete tracking a run."""
        if self._current_lineage:
            self._current_lineage.complete(status)
            self._persist(self._current_lineage)
            logger.info(f"[LINEAGE] Completed run: {self._current_lineage.run_id} - {status}")
            self._current_lineage = None
    
    def complete_lineage(self, status: str = "COMPLETE"):
        """Alias for complete_run - for API compatibility."""
        self.complete_run(status)
    
    def _persist(self, lineage: RunLineage):
        """Persist lineage to disk."""
        filename = f"lineage_{lineage.run_id}_{lineage.start_time.replace(':', '-')}.json"
        filepath = self.lineage_dir / filename
        
        with open(filepath, 'w') as f:
            json.dump(lineage.to_dict(), f, indent=2)
        
        logger.info(f"[LINEAGE] Persisted: {filepath}")
    
    def get_current(self) -> Optional[RunLineage]:
        """Get current lineage being tracked."""
        return self._current_lineage
    
    def load_lineage(self, run_id: str) -> Optional[RunLineage]:
        """Load lineage from disk."""
        for filepath in self.lineage_dir.glob(f"lineage_{run_id}_*.json"):
            with open(filepath) as f:
                data = json.load(f)
                lineage = RunLineage(
                    run_id=data['run_id'],
                    system_version=data['system_version'],
                    prediction_ids=data.get('prediction_ids', []),
                    portfolio_id=data.get('portfolio_id'),
                    risk_id=data.get('risk_id'),
                    policy_id=data.get('policy_id'),
                    execution_id=data.get('execution_id'),
                    model_version=data.get('model_version'),
                    calibration_version=data.get('calibration_version'),
                    decision_version=data.get('decision_version'),
                    policy_version=data.get('policy_version'),
                    start_time=data.get('start_time'),
                    end_time=data.get('end_time'),
                    status=data.get('status')
                )
                return lineage
        return None
    
    def list_runs(self, limit: int = 20) -> list:
        """List recent runs."""
        runs = []
        for filepath in sorted(self.lineage_dir.glob("lineage_*.json"), reverse=True)[:limit]:
            with open(filepath) as f:
                data = json.load(f)
                runs.append({
                    'run_id': data['run_id'],
                    'status': data['status'],
                    'predictions': data.get('prediction_count', 0),
                    'start_time': data['start_time']
                })
        return runs


# Global tracker instance
_lineage_tracker: Optional[LineageTracker] = None


def get_lineage_tracker() -> LineageTracker:
    """Get global lineage tracker."""
    global _lineage_tracker
    if _lineage_tracker is None:
        _lineage_tracker = LineageTracker()
    return _lineage_tracker


def start_lineage(run_id: str) -> RunLineage:
    """Start tracking a run's lineage."""
    return get_lineage_tracker().start_run(run_id)


def complete_lineage(status: str = "COMPLETE"):
    """Complete tracking the current run."""
    get_lineage_tracker().complete_run(status)


def validate_prediction_consistency():
    """
    Validate prediction consistency across the system.
    
    IGNORES legacy predictions (is_legacy=1)
    Only validates NEW predictions (is_legacy=0)
    
    Checks:
    - Mismatched picks between raw_outcome and predicted_outcome
    - Missing odds_snapshot
    - Duplicate predictions for same fixture+market
    - Records without prediction_id
    
    Raises:
        RuntimeError: If consistency violations detected in NEW predictions
    """
    from src.storage.db import get_session
    from src.storage.models import PredictionRecord
    from src.prediction.market_normalizer import normalize_market_pick
    from sqlalchemy import select
    
    errors = []
    warnings = []
    
    with get_session() as s:
        all_predictions = s.execute(
            select(PredictionRecord)
            .where(PredictionRecord.is_legacy == 0)
            .order_by(PredictionRecord.created_at.desc())
        ).scalars().all()
        
        fixture_markets = {}
        
        for pred in all_predictions:
            key = (pred.fixture_id, pred.market)
            
            if key in fixture_markets:
                errors.append(
                    f"Duplicate prediction for fixture={pred.fixture_id}, market={pred.market}"
                )
            else:
                fixture_markets[key] = pred.id
            
            if not pred.prediction_id:
                errors.append(
                    f"Prediction without prediction_id: fixture={pred.fixture_id}, market={pred.market}"
                )
            
            if not pred.odds_snapshot:
                warnings.append(
                    f"Prediction missing odds_snapshot: fixture={pred.fixture_id}, market={pred.market}"
                )
            
            if pred.raw_outcome and pred.predicted_outcome:
                normalized_raw = normalize_market_pick(pred.market, pred.raw_outcome)
                if normalized_raw != pred.predicted_outcome:
                    errors.append(
                        f"Mismatched pick: fixture={pred.fixture_id}, market={pred.market}, "
                        f"raw='{pred.raw_outcome}' vs predicted='{pred.predicted_outcome}'"
                    )
    
    if errors:
        error_msg = f"PREDICTION CONSISTENCY VIOLATION: {len(errors)} error(s) found\n" + "\n".join(errors)
        logger.error(f"[VALIDATION] {error_msg}")
        raise RuntimeError(error_msg)
    
    if warnings:
        logger.warning(f"[VALIDATION] Warnings: {warnings}")
    
    logger.info(f"[VALIDATION] Prediction consistency check passed for {len(all_predictions)} predictions")
    return True