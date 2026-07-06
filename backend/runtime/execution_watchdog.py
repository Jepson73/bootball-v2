#!/usr/bin/env python3
"""
Execution Watchdog - Runtime Health Monitor

Monitors ExecutionRuntime heartbeat and health.
Detects:
- stalled cycles
- silent failures  
- no-prediction cycles
- no-bet cycles when expected

Restarts runtime if needed.

Events emitted:
- RUNTIME_STALLED
- RUNTIME_RECOVERED
- RUNTIME_RESTARTED
- WATCHDOG_ALERT
"""

import os
import sys
import time
import signal
import threading
import logging
import subprocess
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    """Configuration for execution watchdog."""
    HEARTBEAT_TIMEOUT: int = 120
    MAX_CONSECUTIVE_FAILURES: int = 3
    MAX_EMPTY_PREDICTIONS: int = 3
    MAX_EMPTY_BETS: int = 3
    CHECK_INTERVAL: int = 30
    RESTART_BACKOFF: int = 60
    MAX_RESTARTS: int = 5
    
    @classmethod
    def from_env(cls):
        return cls(
            HEARTBEAT_TIMEOUT=int(os.getenv("WATCHDOG_HEARTBEAT_TIMEOUT", "120")),
            MAX_CONSECUTIVE_FAILURES=int(os.getenv("WATCHDOG_MAX_FAILURES", "3")),
            MAX_EMPTY_PREDICTIONS=int(os.getenv("WATCHDOG_MAX_EMPTY_PREDICTIONS", "3")),
            MAX_EMPTY_BETS=int(os.getenv("WATCHDOG_MAX_EMPTY_BETS", "3")),
            CHECK_INTERVAL=int(os.getenv("WATCHDOG_CHECK_INTERVAL", "30")),
            RESTART_BACKOFF=int(os.getenv("WATCHDOG_RESTART_BACKOFF", "60")),
            MAX_RESTARTS=int(os.getenv("WATCHDOG_MAX_RESTARTS", "5")),
        )


class ExecutionWatchdog:
    """
    Execution Runtime Health Monitor.
    
    Monitors ExecutionRuntime and detects failure modes:
    - stalled cycles (no heartbeat)
    - silent failures (repeated crashes)
    - no-prediction cycles
    - no-bet cycles when predictions exist
    
    Actions:
    - Emit events for state changes
    - Auto-restart runtime if needed
    """
    
    def __init__(self, config: WatchdogConfig = None):
        self.config = config or WatchdogConfig.from_env()
        self._running = False
        self._shutdown_event = threading.Event()
        
        self._last_heartbeat: Optional[datetime] = None
        self._consecutive_failures: int = 0
        self._consecutive_empty_predictions: int = 0
        self._consecutive_empty_bets: int = 0
        self._restart_count: int = 0
        
        self._cycle_history: deque = deque(maxlen=100)
        
        self._event_callbacks: list[Callable] = []
        
        self._runtime_pid: Optional[int] = None
        
        logger.info("=" * 60)
        logger.info("EXECUTION WATCHDOG INITIALIZING")
        logger.info(f"Heartbeat Timeout: {self.config.HEARTBEAT_TIMEOUT}s")
        logger.info(f"Max Failures: {self.config.MAX_CONSECUTIVE_FAILURES}")
        logger.info(f"Check Interval: {self.config.CHECK_INTERVAL}s")
        logger.info("=" * 60)
    
    def start(self):
        """Start the watchdog monitoring loop."""
        self._running = True
        logger.info("🚀 Starting watchdog monitoring...")
        
        while self._running and not self._shutdown_event.is_set():
            try:
                self._check_health()
                self._shutdown_event.wait(timeout=self.config.CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("⚠️  Interrupted by user")
                break
            except Exception:
                logger.exception("Watchdog check failed")
                time.sleep(self.config.CHECK_INTERVAL)
        
        self._running = False
        logger.info("👋 Watchdog stopped")
    
    def _check_health(self):
        """Check runtime health and detect failures."""
        from src.infra.runtime_lock import RuntimeLock, verify_execution_ownership
        
        current_time = datetime.utcnow()
        
        has_runtime = verify_execution_ownership()
        runtime_active = RuntimeLock.get_active_instance() is not None
        
        if not runtime_active:
            logger.info("No execution runtime active - no monitoring needed")
            return
        
        self._emit_event("WATCHDOG_CHECK", {
            "timestamp": current_time.isoformat(),
            "has_runtime": has_runtime,
            "runtime_instance": RuntimeLock.get_active_instance(),
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
        })
        
        if self._last_heartbeat:
            time_since_heartbeat = (current_time - self._last_heartbeat).total_seconds()
            
            if time_since_heartbeat > self.config.HEARTBEAT_TIMEOUT:
                self._handle_stalled_runtime(time_since_heartbeat)
        
        if self._consecutive_failures >= self.config.MAX_CONSECUTIVE_FAILURES:
            self._handle_repeated_crashes()
        
        if self._consecutive_empty_predictions >= self.config.MAX_EMPTY_PREDICTIONS:
            self._handle_no_predictions()
        
        if self._consecutive_empty_bets >= self.config.MAX_EMPTY_BETS:
            self._handle_no_bets()
    
    def _handle_stalled_runtime(self, time_since: float):
        """Handle stalled runtime (no heartbeat)."""
        logger.warning(f"⚠️  RUNTIME STALLED: No heartbeat for {time_since:.0f}s")
        
        self._emit_event("RUNTIME_STALLED", {
            "time_since_heartbeat": time_since,
            "threshold": self.config.HEARTBEAT_TIMEOUT,
            "restart_count": self._restart_count,
        })
        
        self._attempt_restart("heartbeat_timeout")
    
    def _handle_repeated_crashes(self):
        """Handle repeated cycle crashes."""
        logger.warning(f"⚠️  REPEATED CRASHES: {self._consecutive_failures} consecutive failures")
        
        self._emit_event("WATCHDOG_ALERT", {
            "alert_type": "repeated_crashes",
            "failure_count": self._consecutive_failures,
            "threshold": self.config.MAX_CONSECUTIVE_FAILURES,
        })
        
        self._attempt_restart("repeated_crashes")
    
    def _handle_no_predictions(self):
        """Handle cycles producing no predictions."""
        logger.warning(f"⚠️  NO PREDICTIONS: {self._consecutive_empty_predictions} empty cycles")
        
        self._emit_event("WATCHDOG_ALERT", {
            "alert_type": "no_predictions",
            "empty_count": self._consecutive_empty_predictions,
            "threshold": self.config.MAX_EMPTY_PREDICTIONS,
        })
    
    def _handle_no_bets(self):
        """Handle cycles with no bets when predictions exist."""
        logger.warning(f"⚠️  NO BETS: {self._consecutive_empty_bets} cycles without bets")
        
        self._emit_event("WATCHDOG_ALERT", {
            "alert_type": "no_bets",
            "empty_count": self._consecutive_empty_bets,
            "threshold": self.config.MAX_EMPTY_BETS,
        })
    
    def _attempt_restart(self, reason: str):
        """Attempt to restart the runtime."""
        if self._restart_count >= self.config.MAX_RESTARTS:
            logger.critical(f"⛔ MAX RESTARTS ({self._config.MAX_RESTARTS}) reached - giving up")
            self._emit_event("WATCHDOG_ALERT", {
                "alert_type": "max_restarts_reached",
                "restart_count": self._restart_count,
            })
            return
        
        logger.info(f"🔄 Attempting restart #{self._restart_count + 1}...")
        
        try:
            from src.infra.runtime_lock import RuntimeLock
            RuntimeLock.release()
        except:
            pass
        
        time.sleep(self.config.RESTART_BACKOFF)
        
        self._restart_count += 1
        
        self._emit_event("RUNTIME_RESTARTED", {
            "reason": reason,
            "restart_number": self._restart_count,
            "max_restarts": self.config.MAX_RESTARTS,
        })
        
        logger.info(f"✅ Restart initiated (attempt {self._restart_count})")
    
    def record_cycle(self, cycle_data: dict):
        """Record a cycle execution for monitoring.
        
        Args:
            cycle_data: dict with keys: timestamp, duration, predictions, bets, success, error
        """
        self._last_heartbeat = datetime.utcnow()
        self._cycle_history.append(cycle_data)
        
        if cycle_data.get("success"):
            self._consecutive_failures = 0

            # Idle heartbeats (sent during inter-cycle sleep) are not real runs;
            # don't count them against the empty-prediction/bet thresholds.
            if cycle_data.get("status") != "idle":
                if cycle_data.get("predictions", 0) == 0:
                    self._consecutive_empty_predictions += 1
                else:
                    self._consecutive_empty_predictions = 0

                if cycle_data.get("bets", 0) == 0:
                    self._consecutive_empty_bets += 1
                else:
                    self._consecutive_empty_bets = 0
        else:
            self._consecutive_failures += 1
            self._consecutive_empty_predictions = 0
            self._consecutive_empty_bets = 0
            
            logger.warning(f"Cycle failed: {cycle_data.get('error', 'unknown')}")
        
        self._emit_event("CYCLE_RECORDED", {
            "predictions": cycle_data.get("predictions", 0),
            "bets": cycle_data.get("bets", 0),
            "success": cycle_data.get("success", False),
            "duration": cycle_data.get("duration", 0),
        })
    
    def register_event_callback(self, callback: Callable):
        """Register a callback for watchdog events."""
        self._event_callbacks.append(callback)
    
    def _emit_event(self, event_type: str, data: dict):
        """Emit a watchdog event."""
        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data,
        }
        
        logger.info(f"EVENT: {event_type} - {data}")
        
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"Event callback failed: {e}")
        
        try:
            from src.events.event_bus import event_bus, Events
            
            event_map = {
                "RUNTIME_STALLED": "health_update",
                "RUNTIME_RECOVERED": "health_update",
                "RUNTIME_RESTARTED": "health_update",
                "WATCHDOG_ALERT": "alert_triggered",
                "CYCLE_RECORDED": "health_update",
            }
            
            event_name = event_map.get(event_type, "health_update")
            if hasattr(Events, event_name):
                event_bus.emit(getattr(Events, event_name), data)
        except Exception as e:
            logger.debug(f"Could not emit EventBus event: {e}")
    
    def get_status(self) -> dict:
        """Get current watchdog status."""
        return {
            "running": self._running,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_empty_predictions": self._consecutive_empty_predictions,
            "consecutive_empty_bets": self._consecutive_empty_bets,
            "restart_count": self._restart_count,
            "cycle_history_count": len(self._cycle_history),
            "config": {
                "heartbeat_timeout": self.config.HEARTBEAT_TIMEOUT,
                "max_failures": self.config.MAX_CONSECUTIVE_FAILURES,
                "max_empty_predictions": self.config.MAX_EMPTY_PREDICTIONS,
                "max_empty_bets": self.config.MAX_EMPTY_BETS,
            }
        }
    
    def stop(self):
        """Request graceful stop."""
        logger.info("Stop requested")
        self._running = False
        self._shutdown_event.set()


_watchdog: Optional[ExecutionWatchdog] = None


def get_watchdog() -> ExecutionWatchdog:
    """Get the global watchdog instance."""
    global _watchdog
    if _watchdog is None:
        _watchdog = ExecutionWatchdog()
    return _watchdog


def main():
    """Entry point for watchdog."""
    logger.info("🚀 Starting Execution Watchdog")
    
    config = WatchdogConfig.from_env()
    watchdog = ExecutionWatchdog(config)
    
    try:
        watchdog.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()