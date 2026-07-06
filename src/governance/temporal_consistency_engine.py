#!/usr/bin/env python3
"""
src/governance/temporal_consistency_engine.py

Temporal Governance Layer - evaluates system evolution across runs.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class SystemState(Enum):
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DEGRADED = "DEGRADED"
    UNSTABLE_OSCILLATING = "UNSTABLE_OSCILLATING"
    NO_LEARNING_DETECTED = "NO_LEARNING_DETECTED"


@dataclass
class RunTemporalState:
    """Temporal state for a single run."""
    run_id: str
    system_version: str
    timestamp: str
    
    # Core metrics
    psi: float = 0.0  # Prediction Stability Index
    pdi: float = 0.0  # Portfolio Drift Index
    rrs: float = 0.0  # Risk Responsiveness Score
    por: float = 0.0  # Policy Oscillation Rate
    scs: float = 0.0  # System Convergence Score
    
    # Delta from previous run
    delta_roi: float = 0.0
    delta_brier: float = 0.0
    delta_ece: float = 0.0
    delta_risk: float = 0.0
    delta_allocation_entropy: float = 0.0
    delta_policy_stability: float = 0.0
    
    # System state
    system_state: str = "UNKNOWN"
    
    # Metadata
    prediction_count: int = 0
    bet_count: int = 0
    policy_decision: str = "UNKNOWN"
    
    def to_dict(self) -> dict:
        return {
            'run_id': self.run_id,
            'system_version': self.system_version,
            'timestamp': self.timestamp,
            'psi': self.psi,
            'pdi': self.pdi,
            'rrs': self.rrs,
            'por': self.por,
            'scs': self.scs,
            'delta_roi': self.delta_roi,
            'delta_brier': self.delta_brier,
            'delta_ece': self.delta_ece,
            'delta_risk': self.delta_risk,
            'delta_allocation_entropy': self.delta_allocation_entropy,
            'delta_policy_stability': self.delta_policy_stability,
            'system_state': self.system_state,
            'prediction_count': self.prediction_count,
            'bet_count': self.bet_count,
            'policy_decision': self.policy_decision
        }


class TemporalConsistencyEngine:
    """
    Evaluates system evolution across multiple runs.
    
    Metrics:
    - PSI: Prediction Stability Index (KL divergence approximation)
    - PDI: Portfolio Drift Index (variance of allocations)
    - RRS: Risk Responsiveness Score (smoothness of λ adaptation)
    - POR: Policy Oscillation Rate (APPROVE/REJECT flips)
    - SCS: System Convergence Score (composite)
    """
    
    def __init__(self, window_size: int = 20, temporal_dir: str = "data/temporal"):
        self.window_size = window_size
        self.temporal_dir = Path(temporal_dir)
        self.temporal_dir.mkdir(parents=True, exist_ok=True)
        
        self._previous_states: list[RunTemporalState] = []
        self._load_previous_states()
    
    def _load_previous_states(self):
        """Load previous temporal states from disk."""
        for filepath in sorted(self.temporal_dir.glob("temporal_state_*.json"), reverse=True)[:self.window_size]:
            with open(filepath) as f:
                data = json.load(f)
                state = RunTemporalState(
                    run_id=data['run_id'],
                    system_version=data['system_version'],
                    timestamp=data['timestamp'],
                    psi=data.get('psi', 0),
                    pdi=data.get('pdi', 0),
                    rrs=data.get('rrs', 0),
                    por=data.get('por', 0),
                    scs=data.get('scs', 0),
                    delta_roi=data.get('delta_roi', 0),
                    delta_brier=data.get('delta_brier', 0),
                    delta_ece=data.get('delta_ece', 0),
                    delta_risk=data.get('delta_risk', 0),
                    delta_allocation_entropy=data.get('delta_allocation_entropy', 0),
                    delta_policy_stability=data.get('delta_policy_stability', 0),
                    system_state=data.get('system_state', 'UNKNOWN'),
                    prediction_count=data.get('prediction_count', 0),
                    bet_count=data.get('bet_count', 0),
                    policy_decision=data.get('policy_decision', 'UNKNOWN')
                )
                self._previous_states.append(state)
        
        logger.info(f"[TEMPORAL] Loaded {len(self._previous_states)} previous states")
    
    # Minimum runs with actual prediction activity before NO_LEARNING_DETECTED can block
    _MIN_ACTIVITY_RUNS_FOR_LEARNING_CHECK = 5

    def evaluate(self, run_id: str, current_run_data: dict, previous_run_data: Optional[dict] = None) -> RunTemporalState:
        """
        Evaluate temporal state for a run.

        Args:
            run_id: The run ID
            current_run_data: Dict with predictions, portfolio, risk, policy, execution
            previous_run_data: Optional dict with previous run data for delta calculation

        Returns:
            RunTemporalState with all temporal metrics
        """
        system_version = current_run_data.get('system_version', 'unknown')

        # Get previous state for deltas
        prev_state = self._previous_states[-1] if self._previous_states else None

        # Compute metrics
        psi = self._compute_psi(current_run_data.get('predictions', []))
        pdi = self._compute_pdi(current_run_data.get('portfolio', []))
        rrs = self._compute_rrs(current_run_data.get('risk_profile', {}))
        por = self._compute_por(current_run_data.get('policy_decision'), prev_state)
        scs = self._compute_scs(psi, pdi, rrs, por)

        # Compute deltas
        delta_roi = self._compute_delta_roi(current_run_data, previous_run_data)
        delta_brier = self._compute_delta_brier(current_run_data, previous_run_data)
        delta_ece = self._compute_delta_ece(current_run_data, previous_run_data)
        delta_risk = self._compute_delta_risk(current_run_data, previous_run_data)
        delta_entropy = self._compute_delta_entropy(current_run_data, previous_run_data)
        delta_policy = self._compute_delta_policy(current_run_data, previous_run_data)

        # Require a baseline of runs with real prediction activity before blocking on
        # NO_LEARNING_DETECTED — a freshly bootstrapped system hasn't had a chance to learn yet
        active_runs = [s for s in self._previous_states if s.prediction_count > 0]
        has_meaningful_history = len(active_runs) >= self._MIN_ACTIVITY_RUNS_FOR_LEARNING_CHECK

        # Determine system state
        system_state = self._classify_system_state(scs, delta_roi, delta_brier, por, has_meaningful_history)
        
        # Create temporal state
        temporal_state = RunTemporalState(
            run_id=run_id,
            system_version=system_version,
            timestamp=datetime.utcnow().isoformat(),
            psi=psi,
            pdi=pdi,
            rrs=rrs,
            por=por,
            scs=scs,
            delta_roi=delta_roi,
            delta_brier=delta_brier,
            delta_ece=delta_ece,
            delta_risk=delta_risk,
            delta_allocation_entropy=delta_entropy,
            delta_policy_stability=delta_policy,
            system_state=system_state.value,
            prediction_count=len(current_run_data.get('predictions', [])),
            bet_count=len(current_run_data.get('portfolio', [])),
            policy_decision=current_run_data.get('policy_decision', 'UNKNOWN')
        )
        
        # Persist state
        self._persist_state(temporal_state)
        
        # Add to history
        self._previous_states.append(temporal_state)
        if len(self._previous_states) > self.window_size:
            self._previous_states = self._previous_states[-self.window_size:]
        
        # Emit governance event
        self._emit_temporal_event(temporal_state)
        
        return temporal_state
    
    def _compute_psi(self, predictions: list) -> float:
        """
        Prediction Stability Index - measures distribution shift.
        
        Uses KL divergence approximation between probability distributions.
        """
        if len(predictions) < 2:
            return 0.0
        
        # Collect probabilities
        probs = []
        for pred in predictions:
            if isinstance(pred, dict):
                prob = pred.get('our_prob', pred.get('calibrated_prob', 0.5))
            else:
                prob = getattr(pred, 'our_prob', 0.5)
            probs.append(prob)
        
        if not probs:
            return 0.0
        
        # Calculate variance as stability measure (lower = more stable)
        variance = np.var(probs) if len(probs) > 1 else 0.0
        
        # Convert to stability index (0 = unstable, 1 = stable)
        psi = max(0, 1 - math.sqrt(variance) * 2)
        
        return psi
    
    def _compute_pdi(self, portfolio: list) -> float:
        """
        Portfolio Drift Index - measures allocation instability.
        
        Uses entropy of market/league distribution.
        """
        if not portfolio:
            return 0.0
        
        # Count allocations per market
        market_counts = {}
        for bet in portfolio:
            market = bet.get('market', 'unknown')
            market_counts[market] = market_counts.get(market, 0) + 1
        
        # Compute Shannon entropy
        total = len(portfolio)
        entropy = 0.0
        for count in market_counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        
        # Normalize to 0-1 (1 = diverse, 0 = concentrated)
        max_entropy = math.log2(len(market_counts)) if market_counts else 1
        pdi = entropy / max_entropy if max_entropy > 0 else 0.0
        
        return pdi
    
    def _compute_rrs(self, risk_profile: dict) -> float:
        """
        Risk Responsiveness Score - measures λ adaptation smoothness.

        Compares current λ to history, penalizes oscillation.
        """
        if not risk_profile:
            return 0.5

        current_lambda = risk_profile.get('lambda', 1.0)

        # delta_risk stores the change-in-risk-score, not the actual lambda value.
        # Only use historical delta_risk values that are non-zero so we don't conflate
        # "system just started and stored 0.0 deltas" with "lambda history = [0, 1]".
        historical = [s.delta_risk for s in self._previous_states[-5:] if s.delta_risk != 0.0]

        if not historical:
            # No meaningful lambda history yet — assume smooth/stable
            return 1.0

        lambdas = historical + [current_lambda]

        if len(lambdas) < 2:
            return 1.0

        # Calculate variance of lambda (lower = smoother adaptation)
        variance = np.var(lambdas)

        # Convert to score (1 = smooth, 0 = oscillating)
        rrs = max(0, 1 - variance * 10)

        return rrs
    
    def _compute_por(self, current_decision: str, prev_state: Optional[RunTemporalState]) -> float:
        """
        Policy Oscillation Rate - tracks APPROVE/REJECT flips.
        """
        if not prev_state:
            return 0.0
        
        prev_decision = prev_state.policy_decision
        
        # Count flips in recent history
        flips = 0
        for i in range(len(self._previous_states) - 1, 0, -1):
            curr = self._previous_states[i].policy_decision
            prev = self._previous_states[i - 1].policy_decision
            if curr != prev:
                flips += 1
        
        # POR = flips / total comparisons
        total = len(self._previous_states) - 1
        por = flips / total if total > 0 else 0.0
        
        return por
    
    def _compute_scs(self, psi: float, pdi: float, rrs: float, por: float) -> float:
        """
        System Convergence Score - composite metric.
        
        SCS = weighted(psi, pdi, rrs, por)
        """
        weights = {
            'psi': 0.3,
            'pdi': 0.2,
            'rrs': 0.3,
            'por': 0.2
        }
        
        # Invert por (high oscillation = bad)
        por_score = 1 - por
        
        scs = (
            weights['psi'] * psi +
            weights['pdi'] * pdi +
            weights['rrs'] * rrs +
            weights['por'] * por_score
        )
        
        return scs
    
    def _compute_delta_roi(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in ROI."""
        if not previous:
            return 0.0
        
        curr_roi = current.get('roi', 0)
        prev_roi = previous.get('roi', 0)
        
        return curr_roi - prev_roi
    
    def _compute_delta_brier(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in Brier score."""
        if not previous:
            return 0.0
        
        curr_brier = current.get('brier_score', 0)
        prev_brier = previous.get('brier_score', 0)
        
        return curr_brier - prev_brier
    
    def _compute_delta_ece(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in ECE."""
        if not previous:
            return 0.0
        
        curr_ece = current.get('ece', 0)
        prev_ece = previous.get('ece', 0)
        
        return curr_ece - prev_ece
    
    def _compute_delta_risk(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in risk metrics."""
        if not previous:
            return 0.0
        
        curr_risk = current.get('risk_score', 0)
        prev_risk = previous.get('risk_score', 0)
        
        return curr_risk - prev_risk
    
    def _compute_delta_entropy(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in allocation entropy."""
        if not previous:
            return 0.0
        
        curr_entropy = self._compute_entropy(current.get('portfolio', []))
        prev_entropy = self._compute_entropy(previous.get('portfolio', []))
        
        return curr_entropy - prev_entropy
    
    def _compute_entropy(self, portfolio: list) -> float:
        """Compute Shannon entropy of portfolio."""
        if not portfolio:
            return 0.0
        
        market_counts = {}
        for bet in portfolio:
            market = bet.get('market', 'unknown')
            market_counts[market] = market_counts.get(market, 0) + 1
        
        total = len(portfolio)
        entropy = 0.0
        for count in market_counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        
        return entropy
    
    def _compute_delta_policy(self, current: dict, previous: Optional[dict]) -> float:
        """Compute delta in policy stability."""
        if not previous:
            return 0.0
        
        # Simple: 1 if same, 0 if different
        curr_decision = current.get('policy_decision')
        prev_decision = previous.get('policy_decision')
        
        return 1.0 if curr_decision == prev_decision else 0.0
    
    def _classify_system_state(self, scs: float, delta_roi: float, delta_brier: float,
                               por: float, has_meaningful_history: bool = True) -> SystemState:
        """Classify system state based on metrics."""

        # Check for oscillation
        if por > 0.5:
            return SystemState.UNSTABLE_OSCILLATING

        # Check for improvement
        if delta_roi > 0.01 and delta_brier < 0:  # ROI up, Brier down (better)
            return SystemState.IMPROVING

        # Check for degradation
        if delta_roi < -0.01 and delta_brier > 0:  # ROI down, Brier up (worse)
            return SystemState.DEGRADED

        # NO_LEARNING_DETECTED requires an established baseline to be meaningful.
        # Zero deltas on a freshly bootstrapped system are absence of evidence, not
        # evidence of stagnation — don't block until we have real history to compare.
        if scs < 0.4 and abs(delta_roi) < 0.001 and abs(delta_brier) < 0.001:
            if has_meaningful_history:
                return SystemState.NO_LEARNING_DETECTED
            logger.debug("[TEMPORAL] Low SCS with zero deltas but insufficient history — classifying as STABLE (bootstrap)")

        # Default: stable
        return SystemState.STABLE
    
    def _persist_state(self, state: RunTemporalState):
        """Persist temporal state to disk."""
        filepath = self.temporal_dir / f"temporal_state_{state.run_id}.json"
        
        with open(filepath, 'w') as f:
            json.dump(state.to_dict(), f, indent=2)
        
        logger.debug(f"[TEMPORAL] Persisted state: {state.run_id}")
    
    def _emit_temporal_event(self, state: RunTemporalState):
        """Emit temporal governance events."""
        from src.events.event_bus import event_bus
        
        event_map = {
            SystemState.IMPROVING: "SYSTEM_IMPROVING",
            SystemState.STABLE: "SYSTEM_STABLE",
            SystemState.DEGRADED: "SYSTEM_DEGRADED",
            SystemState.UNSTABLE_OSCILLATING: "SYSTEM_OSCILLATING",
            SystemState.NO_LEARNING_DETECTED: "SYSTEM_NO_LEARNING"
        }
        
        event_name = event_map.get(SystemState(state.system_state), "SYSTEM_STATE_UNKNOWN")
        
        event_bus.emit(event_name, {
            'run_id': state.run_id,
            'system_state': state.system_state,
            'scs': state.scs,
            'psi': state.psi,
            'pdi': state.pdi,
            'rrs': state.rrs,
            'por': state.por,
            'delta_roi': state.delta_roi,
            'delta_brier': state.delta_brier,
            'timestamp': state.timestamp
        })
        
        logger.info(f"[TEMPORAL] System state: {state.system_state} (SCS={state.scs:.3f})")
    
    def get_system_evolution_report(self) -> str:
        """Generate system evolution report."""
        if not self._previous_states:
            return "# System Evolution Report\n\nNo data available.\n"
        
        recent = self._previous_states[-10:]
        
        report = "# System Evolution Report\n\n"
        report += f"## Recent Runs (last {len(recent)})\n\n"
        report += "| Run | SCS | PSI | PDI | RRS | POR | State |\n"
        report += "|-----|-----|-----|-----|-----|-----|-------|\n"
        
        for state in reversed(recent):
            report += f"| {state.run_id} | {state.scs:.3f} | {state.psi:.3f} | {state.pdi:.3f} | {state.rrs:.3f} | {state.por:.3f} | {state.system_state} |\n"
        
        # Add trend analysis
        report += "\n## Trend Analysis\n\n"
        
        if len(recent) >= 2:
            first = recent[0]
            last = recent[-1]
            
            report += f"- SCS: {first.scs:.3f} → {last.scs:.3f} ({'↑' if last.scs > first.scs else '↓'})\n"
            report += f"- PSI: {first.psi:.3f} → {last.psi:.3f} ({'↑' if last.psi > first.psi else '↓'})\n"
            report += f"- PDI: {first.pdi:.3f} → {last.pdi:.3f} ({'↑' if last.pdi > first.pdi else '↓'})\n"
            report += f"- RRS: {first.rrs:.3f} → {last.rrs:.3f} ({'↑' if last.rrs > first.rrs else '↓'})\n"
            report += f"- POR: {first.por:.3f} → {last.por:.3f} ({'↓' if last.por < first.por else '↑'})\n"
        
        # Add warnings
        if last.por > 0.3:
            report += f"\n⚠️ **WARNING**: High policy oscillation rate ({last.por:.1%})\n"
        
        if last.system_state == SystemState.NO_LEARNING_DETECTED.value:
            report += f"\n⚠️ **WARNING**: No learning detected in recent runs\n"
        
        if last.system_state == SystemState.DEGRADED.value:
            report += f"\n⚠️ **WARNING**: System appears to be degrading\n"
        
        return report
    
    def save_evolution_report(self):
        """Save system evolution report."""
        from pathlib import Path
        
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        
        report = self.get_system_evolution_report()
        filepath = report_dir / "system_evolution_report.md"
        
        with open(filepath, 'w') as f:
            f.write(report)
        
        logger.info(f"[TEMPORAL] Saved evolution report: {filepath}")


# Global instance
_temporal_engine: Optional[TemporalConsistencyEngine] = None


def get_temporal_engine() -> TemporalConsistencyEngine:
    """Get global temporal engine."""
    global _temporal_engine
    if _temporal_engine is None:
        _temporal_engine = TemporalConsistencyEngine()
    return _temporal_engine