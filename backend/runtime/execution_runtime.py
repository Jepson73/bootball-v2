#!/usr/bin/env python3
"""
ExecutionRuntime - INDEPENDENT EXECUTION ENGINE

This is the ONLY system allowed to run:
- scheduled jobs (continuous cycle)
- prediction generation
- portfolio execution
- AgentCoordinator orchestration

PROCESS ISOLATION:
- Run as standalone process: python backend/runtime/execution_runtime.py
- Separate from Flask API process
- No shared scheduler state

RULES:
- Single instance enforcement via RuntimeLock
- Deterministic execution flow
- Crash isolation from API
"""

import os
import sys
import time
import signal
import logging
import threading
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ExecutionConfig:
    """Configuration for execution runtime."""
    CYCLE_INTERVAL: int = 1200  # 20 minutes
    RETRY_BACKOFF: int = 60    # 1 minute
    MAX_RETRIES: int = 3
    SHUTDOWN_TIMEOUT: int = 30
    
    @classmethod
    def from_env(cls):
        return cls(
            CYCLE_INTERVAL=int(os.getenv("EXECUTION_CYCLE_INTERVAL", "1200")),
            RETRY_BACKOFF=int(os.getenv("EXECUTION_RETRY_BACKOFF", "60")),
            MAX_RETRIES=int(os.getenv("EXECUTION_MAX_RETRIES", "3")),
            SHUTDOWN_TIMEOUT=int(os.getenv("EXECUTION_SHUTDOWN_TIMEOUT", "30")),
        )


class ExecutionRuntime:
    """
    INDEPENDENT EXECUTION ENGINE
    
    This is the single authority for:
    - Running continuous prediction cycles
    - Coordinating multi-agent pipeline
    - Triggering portfolio execution
    
    GUARANTEES:
    1. Single execution authority (enforced by RuntimeLock)
    2. Deterministic execution flow
    3. No web-process dependency
    4. Crash isolation
    5. Full observability preserved
    """
    
    def __init__(self, config: ExecutionConfig = None):
        self.config = config or ExecutionConfig.from_env()
        self._running = False
        self._cycle_count = 0
        self._instance_id = f"runtime_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        
        self._watchdog = None
        
        logger.info("=" * 60)
        logger.info("EXECUTION RUNTIME INITIALIZING")
        logger.info(f"Instance ID: {self._instance_id}")
        logger.info(f"Cycle Interval: {self.config.CYCLE_INTERVAL}s")
        logger.info("=" * 60)
    
    def start(self):
        """Start the execution runtime loop."""
        from src.governance.runtime_lock import RuntimeLock
        
        try:
            RuntimeLock.acquire(self._instance_id)
        except RuntimeError as e:
            logger.critical(f"❌ {e}")
            logger.critical("Only ONE execution runtime can run. Exiting.")
            sys.exit(1)
        
        self._running = True
        logger.info("✅ RuntimeLock acquired - single instance enforced")

        self._init_watchdog()
        self._init_discord()
        self._bootstrap_consumers()
        self._start_scheduler()

        self._register_signal_handlers()

        logger.info("🚀 Starting execution loop...")
        
        while self._running and not self._shutdown_event.is_set():
            try:
                self.execute_cycle()
                self._cycle_count += 1
                
                logger.info(f"💤 Sleeping for {self.config.CYCLE_INTERVAL}s...")
                
                # PRODUCTION-GRADE: Update heartbeat during sleep to prevent watchdog stall
                sleep_interval = 60  # Update heartbeat every 60s
                sleep_remaining = self.config.CYCLE_INTERVAL
                while sleep_remaining > 0 and not self._shutdown_event.is_set():
                    actual_sleep = min(sleep_interval, sleep_remaining)
                    self._shutdown_event.wait(timeout=actual_sleep)
                    sleep_remaining -= actual_sleep
                    
                    # Send heartbeat during sleep
                    if self._watchdog:
                        self._watchdog.record_cycle({
                            "timestamp": datetime.utcnow(),
                            "duration": 0,
                            "predictions": 0,
                            "bets": 0,
                            "status": "idle",
                            "success": True
                        })
                
            except KeyboardInterrupt:
                logger.info("⚠️  Interrupted by user")
                break
            except Exception:
                logger.exception("Cycle failed")
                logger.info("Retrying in %ss...", self.config.RETRY_BACKOFF)
                time.sleep(self.config.RETRY_BACKOFF)
        
        self._shutdown()
    
    def execute_cycle(self):
        """
        Execute a single prediction pipeline cycle.
        
        THIS IS THE SINGLE ENTRY POINT for all execution.
        AgentCoordinator.run_cycle() is the ONLY WAY to run predictions.
        
        Flow:
        1. Log RUN_START
        2. Initialize lineage tracking
        3. → AgentCoordinator.run_cycle() [SINGLE ENTRY POINT]
        4. Verify predictions persisted
        5. Log RUN_END
        """
        import uuid
        run_id = str(uuid.uuid4())[:8]
        
        logger.info("=" * 60)
        logger.info(f"RUN_START: run_id={run_id}")
        logger.info(f"CYCLE #{self._cycle_count + 1} STARTING")
        logger.info(f"Instance: {self._instance_id}")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Initialize lineage tracking
        from src.governance.lineage_tracker import start_lineage
        lineage = start_lineage(run_id)
        
        try:
            from src.agents.coordinator import get_agent_coordinator
            
            coordinator = get_agent_coordinator()
            
            logger.info(f"RUN_STAGE: run_id={run_id}, stage=prediction")
            result = coordinator.run_cycle()
            
            duration = time.time() - start_time
            
            # Verify predictions
            prediction_ids = result.get("prediction_ids", []) if isinstance(result, dict) else []
            predictions = result.get("predictions", 0) if isinstance(result, dict) else 0
            bets = result.get("bets", 0) if isinstance(result, dict) else 0
            
            # HARD ASSERTION - predictions must exist
            if predictions == 0:
                logger.error(f"RUN_FAILED: run_id={run_id}, reason=NO_PREDICTIONS")
                from src.governance.lineage_tracker import complete_lineage
                complete_lineage("FAILED")
                raise RuntimeError("PIPELINE DEAD: NO PREDICTIONS GENERATED")
            
            # HARD ASSERTION - run_id must exist
            assert run_id is not None, "RUN_ID_IS_NONE"
            
            # Set run metrics before completing
            from src.governance.lineage_tracker import get_lineage_tracker
            tracker = get_lineage_tracker()
            tracker.set_run_metrics(
                prediction_count=predictions,
                bet_count=bets,
                health_score=1.0  # Success = full health
            )
            
            # Complete lineage - ALWAYS executes
            from src.governance.lineage_tracker import complete_lineage
            complete_lineage("COMPLETE")
            logger.info(f"Lineage completed: run_id={run_id}, predictions={predictions}, bets={bets}")
            
            logger.info("=" * 60)
            logger.info(f"RUN_END: run_id={run_id}, status=success")
            logger.info(f"CYCLE #{self._cycle_count + 1} COMPLETED")
            logger.info(f"Duration: {duration:.2f}s")
            logger.info(f"Predictions: {predictions}, Bets: {bets}")
            logger.info("=" * 60)
            
            self._record_cycle({
                "timestamp": datetime.utcnow(),
                "duration": duration,
                "predictions": predictions,
                "bets": bets,
                "success": True
            })
            self._log_cycle_to_db(run_id, predictions, bets, duration, success=True, error=None)

            self._run_settlement(run_id)

            return result

        except Exception as e:
            duration = time.time() - start_time
            logger.exception("RUN_END: run_id=%s status=failed duration=%.2fs", run_id, duration)

            # ALWAYS mark lineage as failed - even if exception in lineage handling
            try:
                from src.governance.lineage_tracker import get_lineage_tracker, complete_lineage
                tracker = get_lineage_tracker()
                tracker.set_run_metrics(
                    prediction_count=0,
                    bet_count=0,
                    health_score=0.0
                )
                complete_lineage("FAILED")
                logger.info(f"Lineage marked FAILED: run_id={run_id}")
            except Exception:
                logger.exception("CRITICAL: Failed to complete lineage for run_id=%s", run_id)

            self._record_cycle({
                "timestamp": datetime.utcnow(),
                "duration": duration,
                "predictions": 0,
                "bets": 0,
                "success": False,
                "error": str(e)
            })
            self._log_cycle_to_db(run_id, 0, 0, duration, success=False, error=str(e))
            raise
    
    def _init_discord(self):
        """Wire Discord notifications to the event bus."""
        try:
            from src.notifications.discord_system_notifier import wire_to_event_bus, send_test_message
            wire_to_event_bus()
            send_test_message()
            logger.info("✅ Discord notifications active")
        except Exception as e:
            logger.warning(f"Discord init failed (non-fatal): {e}")

    def _bootstrap_consumers(self):
        """Bootstrap event consumers so calibration/retrain events are acted upon."""
        try:
            from src.events.bootstrap import bootstrap_consumers
            bootstrap_consumers()
            logger.info("✅ Event consumers bootstrapped")
        except Exception as e:
            logger.warning(f"Consumer bootstrap failed (non-fatal): {e}")

    def _init_watchdog(self):
        """Initialize and start the execution watchdog."""
        try:
            from backend.runtime.execution_watchdog import ExecutionWatchdog, WatchdogConfig
            from threading import Thread
            
            watchdog_config = WatchdogConfig.from_env()
            self._watchdog = ExecutionWatchdog(watchdog_config)
            
            watchdog_thread = Thread(target=self._watchdog.start, daemon=True)
            watchdog_thread.start()
            
            logger.info("✅ Execution Watchdog started")
        except Exception as e:
            logger.warning(f"Could not start watchdog: {e}")
            self._watchdog = None
    
    def _log_cycle_to_db(self, run_id: str, predictions: int, bets: int, duration: float, success: bool, error: str | None):
        """Write cycle result to ingestion_log so the process monitor can see it."""
        try:
            from src.storage.db import get_session
            from sqlalchemy import text as _text
            summary = f"run_id={run_id} predictions={predictions} bets={bets} duration={duration:.1f}s"
            with get_session() as s:
                s.execute(_text(
                    "INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message) VALUES (:job, :success, :updated, :error)"
                ), {"job": "betting_pipeline", "success": int(success), "updated": bets, "error": error or summary})
                s.commit()
        except Exception as e:
            logger.warning(f"Could not write cycle to ingestion_log: {e}")

    def _log_maintenance_to_db(self, summary: dict | None, success: bool, error: str | None):
        """Write maintenance run result to ingestion_log so the process monitor can see it."""
        try:
            from src.storage.db import get_session
            from sqlalchemy import text as _text
            if summary:
                scores = summary.get("ft_null_goals_fixed", 0)
                leagues = summary.get("orphaned_leagues_fixed", 0)
                skipped = summary.get("orphaned_leagues_skipped", 0)
                total = scores + leagues
                detail = f"scores_fixed={scores} leagues_fixed={leagues} leagues_skipped={skipped}"
            else:
                total = 0
                detail = error
            with get_session() as s:
                s.execute(_text(
                    "INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message) VALUES (:job, :success, :updated, :error)"
                ), {"job": "maintenance", "success": int(success), "updated": total, "error": detail})
                s.commit()
        except Exception as e:
            logger.warning(f"Could not write maintenance run to ingestion_log: {e}")

    def _start_scheduler(self):
        """Start the APScheduler for auxiliary data jobs (fixtures, results, odds, cleanup)."""
        try:
            from backend.scheduler import start_scheduler
            self._scheduler = start_scheduler()
            logger.info("✅ Auxiliary scheduler started")
        except Exception as e:
            logger.warning(f"Could not start auxiliary scheduler (non-fatal): {e}")
            self._scheduler = None

    def _record_cycle(self, cycle_data: dict):
        """Record cycle with watchdog and ingestion_log."""
        if self._watchdog:
            try:
                self._watchdog.record_cycle(cycle_data)
            except Exception as e:
                logger.warning(f"Could not record cycle with watchdog: {e}")

    def _run_settlement(self, run_id: str):
        """Run maintenance (score backfill, orphan fix) then settle bets and predictions."""
        try:
            from src.maintenance import run_maintenance
            summary = run_maintenance(days=30)
            self._log_maintenance_to_db(summary, success=True, error=None)
        except Exception as e:
            logger.warning(f"Maintenance failed (non-fatal): {e}")
            self._log_maintenance_to_db(None, success=False, error=str(e))

        try:
            from src.settlement import settle_placed_bets, settle_predictions
            settled_bets, pnl, details = settle_placed_bets(days=30)
            settled_preds = settle_predictions(days=30)
            if settled_bets or settled_preds:
                logger.info(
                    f"Settlement run_id={run_id}: {settled_bets} bets settled "
                    f"(P/L {pnl:+.2f}), {settled_preds} predictions settled"
                )
        except Exception as e:
            logger.warning(f"Settlement failed (non-fatal): {e}")
    
    def _register_signal_handlers(self):
        """Register graceful shutdown handlers."""
        def handle_shutdown(signum, frame):
            logger.info(f"⚠️  Received signal {signum}, initiating shutdown...")
            self._running = False
            self._shutdown_event.set()
        
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
    
    def _shutdown(self):
        """Graceful shutdown."""
        logger.info("🛑 Shutting down execution runtime...")
        
        from src.governance.runtime_lock import RuntimeLock
        try:
            RuntimeLock.release()
            logger.info("✅ RuntimeLock released")
        except Exception as e:
            logger.warning(f"Could not release lock: {e}")
        
        logger.info("👋 Execution runtime stopped")
    
    def stop(self):
        """Request graceful stop."""
        logger.info("Stop requested")
        self._running = False
        self._shutdown_event.set()


def get_execution_runtime() -> ExecutionRuntime:
    """Get or create the global execution runtime."""
    global _execution_runtime
    
    if _execution_runtime is None:
        _execution_runtime = ExecutionRuntime()
    
    return _execution_runtime


_execution_runtime: Optional[ExecutionRuntime] = None


def main():
    """Entry point for execution runtime."""
    logger.info("🚀 Starting Bootball Execution Runtime")
    
    config = ExecutionConfig.from_env()
    runtime = ExecutionRuntime(config)
    
    try:
        runtime.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()