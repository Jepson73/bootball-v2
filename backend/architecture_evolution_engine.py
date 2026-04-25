import json
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LAYERS = ['model', 'calibration', 'league', 'latent', 'drift', 'risk']
DEFAULT_LAYER_WEIGHTS = {layer: 1.0 for layer in DEFAULT_LAYERS}

SAFETY_THRESHOLDS = {
    'min_validation_score': 0.6,
    'max_risk_delta': 0.05,
    'min_ev_delta': -0.01,
    'min_calibration_delta': -0.05,
    'rollback_safety_score': 0.8
}


@dataclass
class ArchitectureSnapshot:
    architecture_id: str
    parent_id: Optional[str]
    active_layers: List[str]
    layer_weights: Dict[str, float]
    feature_set: Dict[str, Any]
    calibration_stack: Dict[str, Any]
    governance_score: float
    ev_score: float
    risk_score: float
    validation_score: float
    is_candidate: bool
    is_active: bool
    created_at: datetime
    description: str


@dataclass
class ArchitectureProposal:
    current_architecture: str
    proposed_architecture: str
    changes: Dict[str, Any]
    expected_ev_delta: float
    expected_risk_delta: float
    rollback_safety_score: float
    validation_results: Dict[str, Any]


@dataclass
class ShadowSimulationResult:
    architecture_id: str
    baseline_ev: float
    simulated_ev: float
    ev_delta: float
    baseline_roi: float
    simulated_roi: float
    roi_delta: float
    baseline_calibration: float
    simulated_calibration: float
    calibration_delta: float
    baseline_drawdown: float
    simulated_drawdown: float
    drawdown_delta: float
    validation_score: float
    is_safe: bool


class ArchitectureEvolutionEngine:
    """
    Safe Architecture Evolution Engine.
    
    Responsibilities:
    - Architecture versioning and snapshots
    - Proposal generation from governance outputs
    - Shadow simulation for validation
    - Controlled transitions with safety checks
    - Rollback system
    """

    def __init__(self):
        self.safety_thresholds = SAFETY_THRESHOLDS
        self._ensure_base_architecture()

    def _ensure_base_architecture(self) -> None:
        """Ensure base architecture exists."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT architecture_id FROM architecture_versions WHERE is_active = 1"
            ))
            active = result.fetchone()
            
            if not active:
                result = sess.execute(text(
                    "SELECT architecture_id FROM architecture_versions WHERE architecture_id = 'arch_v1'"
                ))
                existing = result.fetchone()
                
                if not existing:
                    sess.execute(text("""
                        INSERT INTO architecture_versions 
                        (architecture_id, parent_id, active_layers, layer_weights, feature_set, 
                         calibration_stack, governance_score, ev_score, risk_score, 
                         validation_score, is_candidate, is_active, description)
                        VALUES ('arch_v1', NULL, :layers, :weights, '{}', '{}', 0.0, 0.0, 0.0, 1.0, 0, 1, 'Initial base architecture')
                    """), {
                        'layers': json.dumps(DEFAULT_LAYERS),
                        'weights': json.dumps(DEFAULT_LAYER_WEIGHTS)
                    })
                    sess.commit()
                    logger.info("Created base architecture arch_v1")

    def get_active_architecture(self) -> Optional[Dict[str, Any]]:
        """Get currently active architecture."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT * FROM architecture_versions WHERE is_active = 1"
            ))
            row = result.fetchone()
            
            if not row:
                return None
            
            return {
                'architecture_id': row[1],
                'parent_id': row[2],
                'active_layers': json.loads(row[3]),
                'layer_weights': json.loads(row[4]),
                'feature_set': json.loads(row[5]),
                'calibration_stack': json.loads(row[6]),
                'governance_score': row[7],
                'ev_score': row[8],
                'risk_score': row[9],
                'validation_score': row[10],
                'created_at': row[13]
            }

    def get_architecture(self, architecture_id: str) -> Optional[Dict[str, Any]]:
        """Get specific architecture by ID."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT * FROM architecture_versions WHERE architecture_id = :id"
            ), {'id': architecture_id})
            row = result.fetchone()
            
            if not row:
                return None
            
            return {
                'architecture_id': row[1],
                'parent_id': row[2],
                'active_layers': json.loads(row[3]),
                'layer_weights': json.loads(row[4]),
                'feature_set': json.loads(row[5]),
                'calibration_stack': json.loads(row[6]),
                'governance_score': row[7],
                'ev_score': row[8],
                'risk_score': row[9],
                'validation_score': row[10],
                'is_candidate': bool(row[11]),
                'is_active': bool(row[12]),
                'created_at': row[13],
                'description': row[14]
            }

    def get_candidates(self) -> List[Dict[str, Any]]:
        """Get all candidate architectures."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT * FROM architecture_versions WHERE is_candidate = 1 ORDER BY created_at DESC"
            ))
            
            candidates = []
            for row in result.fetchall():
                candidates.append({
                    'architecture_id': row[1],
                    'parent_id': row[2],
                    'active_layers': json.loads(row[3]),
                    'layer_weights': json.loads(row[4]),
                    'governance_score': row[7],
                    'ev_score': row[8],
                    'risk_score': row[9],
                    'validation_score': row[10],
                    'created_at': row[13],
                    'description': row[14]
                })
            
            return candidates

    def get_architecture_history(self) -> List[Dict[str, Any]]:
        """Get full architecture evolution history."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT * FROM architecture_versions ORDER BY created_at ASC"
            ))
            
            history = []
            for row in result.fetchall():
                history.append({
                    'architecture_id': row[1],
                    'parent_id': row[2],
                    'active_layers': json.loads(row[3]),
                    'layer_weights': json.loads(row[4]),
                    'governance_score': row[7],
                    'ev_score': row[8],
                    'risk_score': row[9],
                    'validation_score': row[10],
                    'is_candidate': bool(row[11]),
                    'is_active': bool(row[12]),
                    'created_at': row[13],
                    'description': row[14]
                })
            
            return history

    def propose_architecture_update(self, run_id: str) -> ArchitectureProposal:
        """Generate architecture proposal from governance outputs."""
        from backend.system_governance_engine import get_governance_engine
        
        governance = get_governance_engine()
        
        ablation_results = governance.run_full_ablation_analysis(run_id)
        promotion_recs = governance.evaluate_promotion_demotion(run_id)
        
        current = self.get_active_architecture()
        current_id = current['architecture_id'] if current else 'arch_v1'
        
        layers_to_remove = []
        layers_to_merge = []
        reweight_layers = {}
        
        for layer, result in ablation_results.items():
            if result.recommendation == 'remove':
                layers_to_remove.append(layer)
            
            rec = promotion_recs.get(layer, {})
            if rec.get('ev_contribution', 0) > 0.05:
                reweight_layers[layer] = 1.1
            elif rec.get('ev_contribution', 0) < 0:
                reweight_layers[layer] = 0.9
        
        for layer in list(layers_to_remove):
            redundancy = promotion_recs.get(layer, {}).get('redundancy_index', 0)
            if redundancy > 0.7:
                layers_to_merge.append(layer)
                layers_to_remove.remove(layer)
        
        new_layers = [l for l in current['active_layers'] if l not in layers_to_remove]
        new_weights = current['layer_weights'].copy()
        for layer, weight in reweight_layers.items():
            if layer in new_weights:
                new_weights[layer] = round(new_weights[layer] * weight, 2)
        
        current_architecture = current_id
        proposed_id = f"arch_v{self._get_next_version()}"
        
        expected_ev_delta = sum(
            ablation_results[layer].ev_delta
            for layer in layers_to_remove
        )
        
        expected_risk_delta = sum(
            ablation_results[layer].risk_delta
            for layer in layers_to_remove
        )
        
        return ArchitectureProposal(
            current_architecture=current_architecture,
            proposed_architecture=proposed_id,
            changes={
                'remove_layers': layers_to_remove,
                'merge_layers': layers_to_merge,
                'reweight_layers': reweight_layers
            },
            expected_ev_delta=round(expected_ev_delta, 4),
            expected_risk_delta=round(expected_risk_delta, 4),
            rollback_safety_score=0.85,
            validation_results={}
        )

    def _get_next_version(self) -> int:
        """Get next architecture version number."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT architecture_id FROM architecture_versions ORDER BY created_at DESC LIMIT 1"
            ))
            row = result.fetchone()
            
            if not row:
                return 1
            
            current_id = row[0]
            if current_id.startswith('arch_v'):
                return int(current_id.split('_v')[1]) + 1
            
            return 1

    def create_candidate_architecture(
        self,
        parent_id: str,
        active_layers: List[str],
        layer_weights: Dict[str, float],
        feature_set: Dict[str, Any],
        governance_score: float,
        ev_score: float,
        risk_score: float,
        description: str
    ) -> str:
        """Create a candidate architecture."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        version_num = self._get_next_version()
        architecture_id = f"arch_v{version_num}"
        
        with get_session() as sess:
            sess.execute(text("""
                INSERT INTO architecture_versions 
                (architecture_id, parent_id, active_layers, layer_weights, feature_set,
                 calibration_stack, governance_score, ev_score, risk_score, 
                 validation_score, is_candidate, is_active, description)
                VALUES (:id, :parent, :layers, :weights, :features, '{}', :gov, :ev, :risk, 0.0, 1, 0, :desc)
            """), {
                'id': architecture_id,
                'parent': parent_id,
                'layers': json.dumps(active_layers),
                'weights': json.dumps(layer_weights),
                'features': json.dumps(feature_set),
                'gov': governance_score,
                'ev': ev_score,
                'risk': risk_score,
                'desc': description
            })
            sess.commit()
        
        logger.info(f"Created candidate architecture {architecture_id}")
        return architecture_id

    def run_shadow_simulation(
        self,
        architecture_id: str,
        historical_runs: List[str]
    ) -> ShadowSimulationResult:
        """Run shadow simulation on historical data."""
        from backend.system_governance_engine import get_governance_engine
        
        arch = self.get_architecture(architecture_id)
        if not arch:
            raise ValueError(f"Architecture {architecture_id} not found")
        
        if not historical_runs:
            historical_runs = self._get_recent_run_ids(5)
        
        governance = get_governance_engine()
        
        baseline_ev = 0.0
        simulated_ev = 0.0
        baseline_calibration = 0.0
        simulated_calibration = 0.0
        total_predictions = 0
        
        for run_id in historical_runs:
            result = governance.run_full_ablation_analysis(run_id)
            
            for layer in arch['active_layers']:
                if layer in result:
                    baseline_ev += result[layer].baseline_ev
                    simulated_ev += result[layer].ablated_ev
                    baseline_calibration += result[layer].baseline_calibration
                    simulated_calibration += result[layer].ablated_calibration
                    total_predictions += result[layer].prediction_count
        
        if total_predictions > 0:
            baseline_ev /= len(historical_runs)
            simulated_ev /= len(historical_runs)
            baseline_calibration /= len(historical_runs)
            simulated_calibration /= len(historical_runs)
        
        ev_delta = simulated_ev - baseline_ev
        
        roi_delta = arch['ev_score'] - arch.get('prev_ev_score', arch['ev_score'])
        calibration_delta = simulated_calibration - baseline_calibration
        
        baseline_drawdown = 0.1
        simulated_drawdown = baseline_drawdown * (1 + arch['risk_score'])
        drawdown_delta = simulated_drawdown - baseline_drawdown
        
        validation_score = self._compute_validation_score(
            ev_delta, calibration_delta, drawdown_delta
        )
        
        is_safe = self._check_safety_constraints(
            validation_score, ev_delta, calibration_delta
        )
        
        result = ShadowSimulationResult(
            architecture_id=architecture_id,
            baseline_ev=round(baseline_ev, 6),
            simulated_ev=round(simulated_ev, 6),
            ev_delta=round(ev_delta, 6),
            baseline_roi=0.0,
            simulated_roi=0.0,
            roi_delta=round(roi_delta, 4),
            baseline_calibration=round(baseline_calibration, 4),
            simulated_calibration=round(simulated_calibration, 4),
            calibration_delta=round(calibration_delta, 4),
            baseline_drawdown=round(baseline_drawdown, 4),
            simulated_drawdown=round(simulated_drawdown, 4),
            drawdown_delta=round(drawdown_delta, 4),
            validation_score=round(validation_score, 4),
            is_safe=is_safe
        )
        
        self._update_architecture_validation(architecture_id, validation_score, is_safe)
        
        return result

    def _get_recent_run_ids(self, limit: int) -> List[str]:
        """Get recent run IDs."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                f"SELECT run_id FROM experiment_runs ORDER BY start_timestamp DESC LIMIT {limit}"
            ))
            return [row[0] for row in result.fetchall()]

    def _compute_validation_score(
        self,
        ev_delta: float,
        calibration_delta: float,
        drawdown_delta: float
    ) -> float:
        """Compute overall validation score."""
        ev_component = max(0, ev_delta + 0.05) / 0.1 if ev_delta >= -0.05 else 0.0
        calibration_component = max(0, calibration_delta + 0.03) / 0.05 if calibration_delta >= -0.03 else 0.0
        risk_component = max(0, 0.05 - drawdown_delta) / 0.05
        
        return (ev_component * 0.4 + calibration_component * 0.35 + risk_component * 0.25)

    def _check_safety_constraints(
        self,
        validation_score: float,
        ev_delta: float,
        calibration_delta: float
    ) -> bool:
        """Check if architecture passes safety constraints."""
        if validation_score < self.safety_thresholds['min_validation_score']:
            return False
        
        if ev_delta < self.safety_thresholds['min_ev_delta']:
            return False
        
        if calibration_delta < self.safety_thresholds['min_calibration_delta']:
            return False
        
        return True

    def _update_architecture_validation(
        self,
        architecture_id: str,
        validation_score: float,
        is_safe: bool
    ) -> None:
        """Update architecture validation score."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            sess.execute(text("""
                UPDATE architecture_versions 
                SET validation_score = :score
                WHERE architecture_id = :id
            """), {'score': validation_score, 'id': architecture_id})
            sess.commit()

    def apply_architecture(self, architecture_id: str, reason: str = "") -> Dict[str, Any]:
        """Apply architecture with safety checks."""
        arch = self.get_architecture(architecture_id)
        if not arch:
            return {'success': False, 'error': 'Architecture not found'}
        
        if arch['validation_score'] < self.safety_thresholds['min_validation_score']:
            return {'success': False, 'error': 'Validation score below threshold'}
        
        current = self.get_active_architecture()
        from_architecture = current['architecture_id'] if current else None
        
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            if current:
                sess.execute(text("""
                    UPDATE architecture_versions SET is_active = 0 WHERE is_active = 1
                """))
            
            sess.execute(text("""
                UPDATE architecture_versions SET is_active = 1 WHERE architecture_id = :id
            """), {'id': architecture_id})
            
            sess.execute(text("""
                INSERT INTO architecture_transitions 
                (from_architecture, to_architecture, ev_delta, risk_delta, reason, approved, transition_type)
                VALUES (:from, :to, :ev, :risk, :reason, 1, 'upgrade')
            """), {
                'from': from_architecture,
                'to': architecture_id,
                'ev': arch.get('ev_score', 0),
                'risk': arch.get('risk_score', 0),
                'reason': reason
            })
            
            sess.commit()
        
        logger.info(f"Applied architecture {architecture_id}")
        
        return {
            'success': True,
            'architecture_id': architecture_id,
            'from': from_architecture,
            'validation_score': arch['validation_score']
        }

    def rollback_architecture(self, target_architecture_id: str = None) -> Dict[str, Any]:
        """Rollback to previous safe architecture."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            if target_architecture_id:
                target = target_architecture_id
            else:
                result = sess.execute(text("""
                    SELECT to_architecture FROM architecture_transitions 
                    WHERE rolled_back = 0 AND approved = 1
                    ORDER BY timestamp DESC LIMIT 1 OFFSET 1
                """))
                row = result.fetchone()
                if not row:
                    result = sess.execute(text(
                        "SELECT architecture_id FROM architecture_versions WHERE is_active = 0 ORDER BY created_at DESC LIMIT 1"
                    ))
                    row = result.fetchone()
                    if not row:
                        return {'success': False, 'error': 'No architecture to rollback to'}
                target = row[0]
            
            sess.execute(text("""
                UPDATE architecture_versions SET is_active = 0 WHERE is_active = 1
            """))
            
            sess.execute(text("""
                UPDATE architecture_versions SET is_active = 1 WHERE architecture_id = :id
            """), {'id': target})
            
            current = self.get_active_architecture()
            
            sess.execute(text("""
                INSERT INTO architecture_transitions 
                (from_architecture, to_architecture, ev_delta, risk_delta, reason, approved, rolled_back, transition_type)
                VALUES (:from, :to, 0, 0, 'Rollback', 1, 1, 'rollback')
            """), {
                'from': current['architecture_id'] if current else None,
                'to': target
            })
            
            sess.commit()
        
        logger.info(f"Rolled back to architecture {target}")
        
        return {'success': True, 'architecture_id': target}

    def get_transition_history(self) -> List[Dict[str, Any]]:
        """Get architecture transition history."""
        from src.storage.db import get_session
        from sqlalchemy import text
        
        with get_session() as sess:
            result = sess.execute(text(
                "SELECT * FROM architecture_transitions ORDER BY timestamp DESC"
            ))
            
            history = []
            for row in result.fetchall():
                history.append({
                    'from_architecture': row[1],
                    'to_architecture': row[2],
                    'ev_delta': row[3],
                    'risk_delta': row[4],
                    'calibration_delta': row[5],
                    'reason': row[6],
                    'approved': bool(row[7]),
                    'rolled_back': bool(row[8]),
                    'timestamp': row[9],
                    'transition_type': row[10]
                })
            
            return history


def get_evolution_engine() -> ArchitectureEvolutionEngine:
    """Get singleton evolution engine."""
    return ArchitectureEvolutionEngine()