#!/usr/bin/env python3
"""
src/contracts/pipeline_contracts.py

Strict schemas for pipeline stages and contract validators.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class PipelineStage(Enum):
    PREDICTION = "prediction"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    POLICY = "policy"
    EXECUTION = "execution"


class FailureClassification(Enum):
    MODEL_FAILURE = "MODEL_FAILURE"
    DATA_FAILURE = "DATA_FAILURE"
    PIPELINE_CONTRACT_FAILURE = "PIPELINE_CONTRACT_FAILURE"
    RISK_REJECTION = "RISK_REJECTION"
    POLICY_REJECTION = "POLICY_REJECTION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"


class PolicyDecision(Enum):
    APPROVE = "APPROVE"
    THROTTLE = "THROTTLE"
    REJECT = "REJECT"


@dataclass
class PredictionPacket:
    prediction_id: str
    fixture_id: int
    market: str
    model_version: str
    calibration_version: str
    system_version: str
    predicted_probs: dict
    outcome: str
    our_prob: float
    odds: Optional[float] = None       # None = preliminary (no odds yet)
    calibrated_prob: Optional[float] = None
    ev: Optional[float] = None         # None = preliminary
    preliminary: bool = False
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def validate(self) -> bool:
        required = ['prediction_id', 'fixture_id', 'market', 'model_version', 'predicted_probs', 'outcome', 'our_prob']
        for field_name in required:
            if getattr(self, field_name, None) is None:
                raise ContractValidationError(f"PredictionPacket missing required field: {field_name}")
        if not self.predicted_probs:
            raise ContractValidationError("PredictionPacket predicted_probs is empty")
        # Odds are optional for preliminary predictions; validate only when present.
        if self.odds is not None and self.odds < 1.0:
            raise ContractValidationError(f"PredictionPacket invalid odds: {self.odds}")
        return True


@dataclass
class PortfolioPacket:
    allocations: list
    total_exposure: float
    risk_profile: dict
    system_version: str
    prediction_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def validate(self) -> bool:
        if self.allocations is None:
            raise ContractValidationError("PortfolioPacket allocations is None")
        if not isinstance(self.allocations, list):
            raise ContractValidationError(f"PortfolioPacket allocations must be list, got {type(self.allocations)}")
        return True


@dataclass
class RiskPacket:
    lambda_value: float
    regime: str
    approved_allocations: list
    system_version: str
    decision_version: str
    max_exposure_per_fixture: float
    max_total_exposure: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def validate(self) -> bool:
        if self.lambda_value is None:
            raise ContractValidationError("RiskPacket lambda_value is None")
        if self.regime is None:
            raise ContractValidationError("RiskPacket regime is None")
        if self.approved_allocations is None:
            raise ContractValidationError("RiskPacket approved_allocations is None")
        return True


@dataclass
class PolicyPacket:
    decision: PolicyDecision
    constraints_triggered: list
    system_version: str
    policy_version: str
    risk_score: float
    throttle_multiplier: float = 1.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def validate(self) -> bool:
        if self.decision is None:
            raise ContractValidationError("PolicyPacket decision is None")
        if not isinstance(self.decision, PolicyDecision):
            raise ContractValidationError(f"PolicyPacket decision must be PolicyDecision enum, got {type(self.decision)}")
        return True


@dataclass
class ExecutionPacket:
    bets: list
    bankroll_snapshot: dict
    system_version: str
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def validate(self) -> bool:
        if self.bets is None:
            raise ContractValidationError("ExecutionPacket bets is None")
        if not isinstance(self.bets, list):
            raise ContractValidationError(f"ExecutionPacket bets must be list, got {type(self.bets)}")
        return True


@dataclass
class PipelineTrace:
    run_id: str
    system_version: str
    
    prediction_received: bool = False
    portfolio_generated: bool = False
    risk_evaluated: bool = False
    policy_decided: bool = False
    execution_completed: bool = False
    
    prediction_count: int = 0
    portfolio_count: int = 0
    risk_count: int = 0
    policy_decision: Optional[PolicyDecision] = None
    execution_count: int = 0
    
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    
    start_time: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    
    def mark_prediction(self, count: int):
        self.prediction_received = True
        self.prediction_count = count
        logger.info(f"[TRACE] Predictions received: {count}")
    
    def mark_portfolio(self, count: int):
        self.portfolio_generated = True
        self.portfolio_count = count
        logger.info(f"[TRACE] Portfolio generated: {count}")
    
    def mark_risk(self, count: int):
        self.risk_evaluated = True
        self.risk_count = count
        logger.info(f"[TRACE] Risk evaluated: {count}")
    
    def mark_policy(self, decision):
        self.policy_decided = True
        self.policy_decision = decision
        logger.info(f"[TRACE] Policy decided: {decision}")
    
    def mark_execution(self, count: int):
        self.execution_completed = True
        self.execution_count = count
        logger.info(f"[TRACE] Execution completed: {count}")
    
    def add_failure(self, stage: PipelineStage, classification: FailureClassification, message: str):
        self.failures.append({
            "stage": stage.value,
            "classification": classification.value,
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        })
        logger.error(f"[TRACE] FAILURE at {stage.value}: {classification.value} - {message}")
    
    def add_warning(self, stage: PipelineStage, message: str):
        self.warnings.append({
            "stage": stage.value,
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        })
        logger.warning(f"[TRACE] WARNING at {stage.value}: {message}")
    
    def mark_complete(self):
        self.end_time = datetime.utcnow().isoformat()
        start = datetime.fromisoformat(self.start_time)
        end = datetime.fromisoformat(self.end_time)
        self.duration_seconds = (end - start).total_seconds()
    
    def is_complete(self) -> bool:
        return (
            self.prediction_received and 
            self.portfolio_generated and 
            self.risk_evaluated and 
            self.policy_decided
        )
    
    def is_successful(self) -> bool:
        return self.is_complete() and self.policy_decision == PolicyDecision.APPROVE
    
    def get_status(self) -> str:
        if self.is_successful():
            return "COMPLETE"
        elif self.policy_decision == PolicyDecision.REJECT:
            return "POLICY_REJECTED"
        elif self.failures:
            return "PIPELINE_INCOMPLETE"
        else:
            return "UNKNOWN"


class ContractValidationError(Exception):
    """Raised when a contract validation fails."""
    pass


class ContractValidator:
    """Validates contracts at layer boundaries."""
    
    @staticmethod
    def validate_prediction_input(predictions: list) -> list:
        """Validate predictions entering pipeline."""
        if predictions is None:
            raise ContractValidationError("CONTRACT FAILURE: predictions is None")
        if not isinstance(predictions, list):
            raise ContractValidationError(f"CONTRACT FAILURE: predictions must be list, got {type(predictions)}")
        
        if len(predictions) == 0:
            raise ContractValidationError("CONTRACT FAILURE: Empty predictions list")
        
        validated = []
        for pred in predictions:
            if isinstance(pred, dict):
                pkt = PredictionPacket(
                    prediction_id=pred.get('prediction_id', ''),
                    fixture_id=pred.get('fixture_id'),
                    market=pred.get('market'),
                    model_version=pred.get('model_version', 'unknown'),
                    calibration_version=pred.get('calibration_version', 'unknown'),
                    system_version=pred.get('system_version', 'unknown'),
                    predicted_probs=pred.get('predicted_probs', {}),
                    odds=pred.get('odds'),           # None is valid (preliminary)
                    outcome=pred.get('outcome', ''),
                    our_prob=pred.get('our_prob', 0),
                    calibrated_prob=pred.get('calibrated_prob'),
                    ev=pred.get('ev'),               # None is valid (preliminary)
                    preliminary=bool(pred.get('preliminary', False)),
                )
            elif isinstance(pred, PredictionPacket):
                pkt = pred
            else:
                raise ContractValidationError(f"CONTRACT FAILURE: Invalid prediction type {type(pred)}")
            
            pkt.validate()
            validated.append(pkt)
        
        logger.info(f"[CONTRACT] Validated {len(validated)} prediction packets")
        return validated
    
    @staticmethod
    def validate_portfolio_input(predictions: list, portfolio: list) -> list:
        """Validate portfolio input matches predictions."""
        if not predictions:
            raise ContractValidationError("CONTRACT FAILURE: No predictions for portfolio")
        
        if portfolio is None:
            raise ContractValidationError("CONTRACT FAILURE: portfolio is None")
        if not isinstance(portfolio, list):
            raise ContractValidationError(f"CONTRACT FAILURE: portfolio must be list")
        
        logger.info(f"[CONTRACT] Portfolio validated: {len(portfolio)} allocations")
        return portfolio
    
    @staticmethod
    def validate_risk_input(portfolio: list, risk: dict) -> dict:
        """Validate risk input."""
        if not portfolio:
            raise ContractValidationError("CONTRACT FAILURE: No portfolio for risk evaluation")
        
        if risk is None:
            raise ContractValidationError("CONTRACT FAILURE: risk is None")
        
        logger.info(f"[CONTRACT] Risk input validated")
        return risk
    
    @staticmethod
    def validate_policy_input(risk: dict, policy: dict) -> dict:
        """Validate policy input."""
        if risk is None:
            raise ContractValidationError("CONTRACT FAILURE: risk is None for policy")
        
        if policy is None:
            raise ContractValidationError("CONTRACT FAILURE: policy is None")
        
        logger.info(f"[CONTRACT] Policy input validated")
        return policy
    
    @staticmethod
    def validate_execution_input(policy: dict, execution: list) -> list:
        """Validate execution input."""
        if policy is None:
            raise ContractValidationError("CONTRACT FAILURE: policy is None for execution")
        
        if execution is None:
            raise ContractValidationError("CONTRACT FAILURE: execution is None")
        
        logger.info(f"[CONTRACT] Execution validated: {len(execution)} bets")
        return execution


def get_trace_report(trace: PipelineTrace) -> str:
    """Generate trace report for a run."""
    status = trace.get_status()
    
    pred_status = "✓" if trace.prediction_received else "✗"
    port_status = "✓" if trace.portfolio_generated else "✗"
    risk_status = "✓" if trace.risk_evaluated else "✗"
    policy_status = "✓" if trace.policy_decided else "✗"
    exec_status = "✓" if trace.execution_completed else "✗"
    
    policy_val = trace.policy_decision.value if trace.policy_decision else "N/A"
    
    report = f"""# Pipeline Trace Report

**Run ID**: {trace.run_id}
**System Version**: {trace.system_version}
**Status**: {status}
**Duration**: {trace.duration_seconds:.2f}s

---

## Stage Completion

| Stage | Status | Count |
|-------|--------|-------|
| Prediction | {pred_status} | {trace.prediction_count} |
| Portfolio | {port_status} | {trace.portfolio_count} |
| Risk | {risk_status} | {trace.risk_count} |
| Policy | {policy_status} | {policy_val} |
| Execution | {exec_status} | {trace.execution_count} |

---

## Failures

"""
    
    if trace.failures:
        for f in trace.failures:
            report += f"- **{f['stage']}**: {f['classification']} - {f['message']}\n"
    else:
        report += "None\n"
    
    report += "\n## Warnings\n\n"
    
    if trace.warnings:
        for w in trace.warnings:
            report += f"- **{w['stage']}**: {w['message']}\n"
    else:
        report += "None\n"
    
    return report