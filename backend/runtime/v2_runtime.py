#!/usr/bin/env python3
"""
V2ExecutionRuntime — Phase 31 Part C.

The V2-owned replacement for AgentCoordinator's live cycle, per OWNERSHIP.md. Runs
src.prediction.prediction_cycle.run_prediction_cycle() on the same 20-minute cadence
AgentCoordinator used, instead of the ~1050-line AgentCoordinator pipeline (see
OWNERSHIP.md's "Key finding" for why: only prediction generation + calibration ingest
ever had a live effect there).

PARALLEL-VERIFICATION WINDOW (Phase 31 Part C, temporary):
- Uses its own RuntimeLock file (data/v2_execution_runtime.lock), distinct from
  bootball-runtime.service's (data/execution_runtime.lock), so both can run
  concurrently while V1 is still live. At Part D's cutover, once V1 is retired,
  there is only one runtime again and this distinction stops mattering.
- Prediction/calibration writes are gated by V2_RUNTIME_WRITE_ENABLED (default
  "false" — dry-run only, generates but does not save, does not run calibration
  ingest). Flip to "true" only for the deliberate, supervised parity-verification
  cycle described in OWNERSHIP.md's parity plan; both this process and
  AgentCoordinator will then be writing predictions and calling calibration ingest
  concurrently on the same 20-minute cadence — this is intentional, to observe the
  Phase 28 calibration high-water-mark dedup behavior under real concurrency rather
  than assuming it from a code read.

CUTOVER (Phase 31 Part D, D9): starts backend/scheduler.py's auxiliary APScheduler
(fixtures/results/odds/cleanup/live_settle/daily_sanity_check/v2_collection_heartbeat)
directly, same as ExecutionRuntime used to. This code change ships ahead of the actual
cutover — do not restart bootball-v2-runtime.service with it live while
bootball-runtime.service is still running, or both processes will register jobs
against the same backend/scheduler.py jobstore (data/scheduler.db) and double-execute
ingestion. The restart that picks this up happens at D10, synchronized with stopping V1.
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

V2_LOCK_FILE = "/opt/projects/bootball/data/v2_execution_runtime.lock"


def _write_enabled() -> bool:
    return os.getenv("V2_RUNTIME_WRITE_ENABLED", "false").strip().lower() == "true"


@dataclass
class V2RuntimeConfig:
    CYCLE_INTERVAL: int = 1200  # 20 minutes — matches AgentCoordinator's cadence
    RETRY_BACKOFF: int = 60
    SHUTDOWN_TIMEOUT: int = 30

    @classmethod
    def from_env(cls):
        return cls(
            CYCLE_INTERVAL=int(os.getenv("V2_RUNTIME_CYCLE_INTERVAL", "1200")),
            RETRY_BACKOFF=int(os.getenv("V2_RUNTIME_RETRY_BACKOFF", "60")),
            SHUTDOWN_TIMEOUT=int(os.getenv("V2_RUNTIME_SHUTDOWN_TIMEOUT", "30")),
        )


class V2ExecutionRuntime:
    def __init__(self, config: V2RuntimeConfig = None):
        self.config = config or V2RuntimeConfig.from_env()
        self._running = False
        self._cycle_count = 0
        self._instance_id = f"v2_runtime_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self._shutdown_event = threading.Event()
        self._watchdog = None
        self._scheduler = None

        logger.info("=" * 60)
        logger.info("V2 EXECUTION RUNTIME INITIALIZING")
        logger.info(f"Instance ID: {self._instance_id}")
        logger.info(f"Cycle Interval: {self.config.CYCLE_INTERVAL}s")
        logger.info(f"Write enabled: {_write_enabled()}")
        logger.info("=" * 60)

    def start(self):
        from src.infra.runtime_lock import RuntimeLock

        try:
            RuntimeLock.acquire(self._instance_id, lock_file=V2_LOCK_FILE)
        except RuntimeError as e:
            logger.critical(f"❌ {e}")
            logger.critical("Only ONE V2 runtime can run. Exiting.")
            sys.exit(1)

        self._running = True
        logger.info("✅ V2 RuntimeLock acquired - single instance enforced")

        self._init_watchdog()
        self._init_discord()
        self._bootstrap_consumers()
        self._start_scheduler()
        self._register_signal_handlers()

        logger.info("🚀 Starting V2 prediction-cycle loop...")

        while self._running and not self._shutdown_event.is_set():
            try:
                self.execute_cycle()
                self._cycle_count += 1

                logger.info(f"💤 Sleeping for {self.config.CYCLE_INTERVAL}s...")
                sleep_interval = 60
                sleep_remaining = self.config.CYCLE_INTERVAL
                while sleep_remaining > 0 and not self._shutdown_event.is_set():
                    actual_sleep = min(sleep_interval, sleep_remaining)
                    self._shutdown_event.wait(timeout=actual_sleep)
                    sleep_remaining -= actual_sleep
                    if self._watchdog:
                        self._watchdog.record_cycle({
                            "timestamp": datetime.utcnow(),
                            "duration": 0,
                            "predictions": 0,
                            "bets": 0,
                            "status": "idle",
                            "success": True,
                        })

            except KeyboardInterrupt:
                logger.info("⚠️  Interrupted by user")
                break
            except Exception:
                logger.exception("V2 cycle failed")
                logger.info("Retrying in %ss...", self.config.RETRY_BACKOFF)
                time.sleep(self.config.RETRY_BACKOFF)

        self._shutdown()

    def execute_cycle(self):
        import uuid
        from src.prediction.prediction_cycle import run_prediction_cycle

        run_id = str(uuid.uuid4())[:8]
        write = _write_enabled()

        logger.info("=" * 60)
        logger.info(f"V2_RUN_START: run_id={run_id} write_enabled={write}")
        logger.info(f"CYCLE #{self._cycle_count + 1} STARTING")
        logger.info("=" * 60)

        start_time = time.time()
        result = run_prediction_cycle(save=write, run_id=run_id)
        duration = time.time() - start_time

        logger.info(
            f"V2_RUN_END: run_id={run_id} fixtures={result['fixtures']} "
            f"predictions={result['predictions']} saved={len(result['saved_ids'])} "
            f"calibration_new_outcomes={result['calibration_new_outcomes']} "
            f"duration={duration:.2f}s"
        )

        if self._watchdog:
            try:
                self._watchdog.record_cycle({
                    "timestamp": datetime.utcnow(),
                    "duration": duration,
                    "predictions": result["predictions"],
                    "bets": 0,
                    "success": True,
                })
            except Exception as e:
                logger.warning(f"Could not record cycle with watchdog: {e}")

        return result

    def _init_watchdog(self):
        try:
            from backend.runtime.execution_watchdog import ExecutionWatchdog, WatchdogConfig
            from threading import Thread

            watchdog_config = WatchdogConfig.from_env()
            self._watchdog = ExecutionWatchdog(watchdog_config)
            Thread(target=self._watchdog.start, daemon=True).start()
            logger.info("✅ V2 Execution Watchdog started")
        except Exception as e:
            logger.warning(f"Could not start V2 watchdog: {e}")
            self._watchdog = None

    def _init_discord(self):
        try:
            from src.notifications.v2_discord_notifier import wire_v2_notifier
            wire_v2_notifier()
            logger.info("✅ V2 Discord notifications active")
        except Exception as e:
            logger.warning(f"V2 Discord init failed (non-fatal): {e}")

    def _bootstrap_consumers(self):
        try:
            from src.events.bootstrap import bootstrap_consumers
            bootstrap_consumers()
            logger.info("✅ Event consumers bootstrapped (V2 process)")
        except Exception as e:
            logger.warning(f"Consumer bootstrap failed (non-fatal): {e}")

    def _start_scheduler(self):
        """Start the APScheduler for auxiliary data jobs (fixtures, results, odds, cleanup)."""
        try:
            from backend.scheduler import start_scheduler
            self._scheduler = start_scheduler()
            logger.info("✅ Auxiliary scheduler started (V2 process)")
        except Exception as e:
            logger.warning(f"Could not start auxiliary scheduler (non-fatal): {e}")
            self._scheduler = None

    def _register_signal_handlers(self):
        def handle_shutdown(signum, frame):
            logger.info(f"⚠️  Received signal {signum}, initiating shutdown...")
            self._running = False
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

    def _shutdown(self):
        logger.info("🛑 Shutting down V2 execution runtime...")
        from src.infra.runtime_lock import RuntimeLock
        try:
            RuntimeLock.release()
            logger.info("✅ V2 RuntimeLock released")
        except Exception as e:
            logger.warning(f"Could not release V2 lock: {e}")
        logger.info("👋 V2 execution runtime stopped")

    def stop(self):
        logger.info("Stop requested")
        self._running = False
        self._shutdown_event.set()


_v2_execution_runtime: Optional[V2ExecutionRuntime] = None


def main():
    logger.info("🚀 Starting Bootball V2 Execution Runtime")

    from src.deploy_info import record_running_commit
    commit = record_running_commit("bootball-v2-runtime.service")
    logger.info(f"Running from commit {commit or 'UNKNOWN'}")

    config = V2RuntimeConfig.from_env()
    runtime = V2ExecutionRuntime(config)

    try:
        runtime.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
