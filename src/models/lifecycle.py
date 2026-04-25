"""
Model Lifecycle Manager - orchestrates automated retraining and versioning.

Handles:
- Retraining trigger evaluation
- Job lifecycle management
- Version promotion/demotion
- Full event emission
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


class ModelLifecycleManager:
    """
    Manages the complete model lifecycle from detection to deployment.
    
    Does NOT block betting pipeline - runs asynchronously.
    """
    
    def __init__(self):
        self.active_jobs = {}  # job_id -> job state
        self.version_history = []  # full lineage
        
        # Thresholds (could be moved to config)
        self.drift_threshold = 0.15
        self.roi_degradation_threshold = 3.0  # percent
        self.calibration_threshold = 0.10
        
        logger.info("ModelLifecycleManager initialized")
    
    def evaluate_retrain_trigger(
        self,
        drift_report: dict,
        performance_report: dict
    ) -> dict:
        """
        Evaluate if retraining should be triggered.
        
        Args:
            drift_report: Output from DriftDetector
            performance_report: Output from ModelEvaluator
            
        Returns:
            Dict with trigger decision and reason
        """
        should_retrain = False
        reasons = []
        severity = "low"
        
        # Check drift
        if drift_report:
            for detection in drift_report.get("detections", []):
                if detection.get("severity") == "high":
                    should_retrain = True
                    severity = "high"
                    reasons.append(f"High {detection['type']} (score: {detection['score']:.2f})")
                elif detection.get("severity") == "medium" and not should_retrain:
                    should_retrain = True
                    severity = "medium"
                    reasons.append(f"Medium {detection['type']} (score: {detection['score']:.2f})")
        
        # Check performance degradation
        if performance_report:
            roi = performance_report.get("roi", 0)
            if roi < -self.roi_degradation_threshold:
                should_retrain = True
                severity = "high"
                reasons.append(f"ROI degradation: {roi:.2f}%")
            
            # Check calibration
            calibration_error = performance_report.get("calibration_error", 0)
            if calibration_error > self.calibration_threshold:
                should_retrain = True
                severity = "high"
                reasons.append(f"Calibration error: {calibration_error:.4f}")
        
        return {
            "should_retrain": should_retrain,
            "severity": severity,
            "reasons": reasons,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def start_retraining(
        self,
        market: str,
        context: dict
    ) -> str:
        """
        Start a retraining job.
        
        Args:
            market: Market to retrain (h2h, btts, ou25, etc.)
            context: Context including drift_report, performance_report
            
        Returns:
            job_id for tracking
        """
        job_id = f"retrain-{market}-{uuid.uuid4().hex[:8]}"
        
        # Create job state
        job_state = {
            "job_id": job_id,
            "market": market,
            "status": "started",
            "started_at": datetime.utcnow().isoformat(),
            "context": context,
            "parent_version": context.get("current_version"),
            "progress": 0,
        }
        
        self.active_jobs[job_id] = job_state
        
        # Emit event
        event_bus.emit(Events.MODEL_TREND, {
            "job_id": job_id,
            "market": market,
            "status": "started",
            "reason": context.get("reasons", []),
            "parent_version": context.get("current_version"),
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.info(f"Started retraining job {job_id} for market {market}")
        
        return job_id
    
    def update_progress(
        self,
        job_id: str,
        progress: int,
        status: str = "running"
    ) -> None:
        """
        Update job progress.
        
        Args:
            job_id: Job to update
            progress: Progress percentage 0-100
            status: Current status
        """
        if job_id not in self.active_jobs:
            logger.warning(f"Unknown job_id: {job_id}")
            return
        
        self.active_jobs[job_id]["progress"] = progress
        self.active_jobs[job_id]["status"] = status
        
        # Emit progress event
        event_bus.emit(Events.MODEL_TREND, {
            "job_id": job_id,
            "market": self.active_jobs[job_id]["market"],
            "status": "progress",
            "progress": progress,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.debug(f"Job {job_id} progress: {progress}%")
    
    def finalize_retraining(
        self,
        job_id: str,
        result: dict
    ) -> dict:
        """
        Finalize a retraining job.
        
        Args:
            job_id: Job to finalize
            result: Result including new version metrics
            
        Returns:
            Finalization result
        """
        if job_id not in self.active_jobs:
            return {"error": f"Unknown job_id: {job_id}"}
        
        job_state = self.active_jobs[job_id]
        
        # Check success
        success = result.get("success", False)
        
        if success:
            job_state["status"] = "completed"
            job_state["completed_at"] = datetime.utcnow().isoformat()
            job_state["new_version"] = result.get("version_id")
            job_state["metrics"] = result.get("metrics", {})
            
            # Emit success event
            event_bus.emit(Events.MODEL_TREND, {
                "job_id": job_id,
                "market": job_state["market"],
                "status": "completed",
                "new_version": result.get("version_id"),
                "metrics": result.get("metrics", {}),
                "promoted": result.get("promoted", False),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            logger.info(f"Job {job_id} completed, version {result.get('version_id')} created")
        else:
            job_state["status"] = "failed"
            job_state["completed_at"] = datetime.utcnow().isoformat()
            job_state["error"] = result.get("error", "Unknown error")
            
            # Emit failure event
            event_bus.emit(Events.MODEL_TREND, {
                "job_id": job_id,
                "market": job_state["market"],
                "status": "failed",
                "error": result.get("error"),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            logger.error(f"Job {job_id} failed: {result.get('error')}")
        
        return job_state
    
    def promote_version(
        self,
        version_id: str,
        market: str
    ) -> dict:
        """
        Promote a model version to active status.
        
        Args:
            version_id: Version to promote
            market: Market for version
            
        Returns:
            Promotion result
        """
        # Emit promotion event
        event_bus.emit(Events.MODEL_TREND, {
            "version_id": version_id,
            "market": market,
            "status": "promoted",
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.info(f"Version {version_id} promoted for market {market}")
        
        return {
            "success": True,
            "version_id": version_id,
            "market": market,
            "promoted_at": datetime.utcnow().isoformat()
        }
    
    def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get status of a job."""
        return self.active_jobs.get(job_id)
    
    def get_active_jobs(self) -> list[dict]:
        """Get all active jobs."""
        return [
            {"job_id": jid, **state}
            for jid, state in self.active_jobs.items()
            if state.get("status") in ["started", "running"]
        ]


# Global instance
_lifecycle_manager: Optional[ModelLifecycleManager] = None


def get_lifecycle_manager() -> ModelLifecycleManager:
    """Get global lifecycle manager."""
    global _lifecycle_manager
    if _lifecycle_manager is None:
        _lifecycle_manager = ModelLifecycleManager()
    return _lifecycle_manager
