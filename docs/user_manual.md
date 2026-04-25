# Bootball User Manual

This is the comprehensive technical manual for the Bootball betting intelligence platform. It describes the complete architecture, event system, operational procedures, and the automated self-improvement system.

## A) System Overview

Bootball is a fully event-driven autonomous betting intelligence platform with closed-loop learning. It combines machine learning models with immutable event sourcing to generate value bets, track performance, detect model drift, and automatically improve through self-training.

### Architecture Summary

The system is built on an **event-driven architecture** where:

1. **Events are the single source of truth** - All state changes are captured as immutable events
2. **Pipelines emit events, they do not mutate state directly** - The daily pipeline generates predictions and bets, then emits events describing what happened
3. **Consumers handle all side effects** - Discord alerts, dashboard updates, and health monitoring are handled by separate consumers that subscribe to events
4. **State is reconstructed from events** - Any point in time can be recreated by replaying events from the beginning
5. **Self-improvement through automation** - Drift detection triggers automatic model retraining

### Core Philosophy

The fundamental principle is **immutable event history with closed-loop learning**:

```
Pipeline (daily_run) → Events → State Reconstruction
                            ↓
                       Consumers
                            ↓
                       Dashboards
                            ↓
                    Monitoring (Drift Detection)
                            ↓
                    Retraining Trigger
                            ↓
                    Model Training
                            ↓
                    Version Evaluation
                            ↓
                    New Predictions (Loop)
```

This design provides:
- **Determinism**: Replaying events always produces identical state
- **Auditability**: Every decision is traceable through event history
- **Testability**: Consumers can be tested in isolation
- **Extensibility**: New consumers can be added without modifying pipelines
- **Self-Improvement**: Automated model retraining on drift detection

---

## B) Event System Explanation

### What Events Exist

The system uses a canonical set of event types defined in `src/alerts/event_bus.py`:

#### Betting System Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `bets_generated` | Value bets identified | `run_id`, `bets` array with fixture_id, market, outcome, odds, ev, stake |
| `bet_settled` | Bets resolved after match | `run_id`, `settled_count`, `pnl_total`, `wins`, `losses` |
| `bets_settled` | Batch bet settlement | `run_id`, `settled_bets` array |

#### Run System Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `run_started` | Pipeline execution begins | `run_id`, `mode`, `timestamp` |
| `run_finished` | Pipeline execution completes | `run_id`, `mode`, `total_bets`, `total_ev`, `errors`, `duration` |
| `predictions_generated` | ML predictions made | `run_id`, `fixture_count`, `prediction_count` |

#### Health System Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `health_update` | System health snapshot | `health_score`, `error_rate` |

#### Model System Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `model_trend` | Model lifecycle events | `job_id`, `market`, `status`, `metrics`, `new_version`, `promoted` |

#### Monitoring Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `drift_detected` | Model drift detected | `detection_type`, `severity`, `score`, `details` |
| `market_shift_detected` | Market profitability shift | `market`, `severity`, `score`, `details` |
| `roi_anomaly_detected` | ROI anomaly detected | `severity`, `score`, `details` |

#### Notification Events

| Event Type | Description | Payload |
|------------|--------------|---------|
| `notification_discord` | Discord webhook payload | `title`, `description`, `severity` |
| `state_changed` | Dashboard state update | `type`, `subtype`, `data` |

### How Events Flow

```
daily_run.py
    │
    ├─► fetch fixtures
    ├─► fetch odds  
    ├─► run predictions
    ├─► detect value bets
    │
    └─► EventBus.emit() ─────────────────────────► EventStore (persisted)
            │                                              │
            └─► Consumers (side effects)                    │
                    │                                      │
                    ├─► DiscordConsumer (alerts)           │
                    ├─► BettingDashboardConsumer            │
                    ├─► HealthDashboardConsumer            │
                    ├─► ModelTrendConsumer                 │
                    └─► ModelLifecycleConsumer              │
                                                      │
                                             StateReconstructor (replay)
                                                      │
                                             Dashboards (derived state)
```

### EventBus Behavior

The EventBus (`src/alerts/event_bus.py`) is the central event dispatcher:

1. **emit()** - Called by pipelines to publish events
2. **Logs events** - Keeps in-memory buffer of last 1000 events
3. **Dispatches to registry** - Calls ConsumerRegistry to notify consumers
4. **Persists to EventStore** - Writes to JSONL file for replay

```python
from src.alerts.event_bus import event_bus, Events

# Emit an event
event_bus.emit(Events.BETS_GENERATED, {
    "run_id": "run-123",
    "bets": [
        {"fixture_id": 1, "market": "h2h", "outcome": "H", "odds": 2.1, "ev": 0.06, "stake": 10.0}
    ]
})
```

### Event Store Immutability

The EventStore (`src/events/event_store.py`) is an append-only log:

- Events are written to `/opt/projects/bootball/data/events.jsonl`
- Each line is a JSON object representing one event
- Events are NEVER modified or deleted
- This enables deterministic replay from any point

```python
from src.events.event_store import get_event_store

store = get_event_store()

# Get all events
events = store.get_all_events()

# Get events after a time
from datetime import datetime
events = store.get_events(since=datetime(2025, 1, 1))

# Get events for a specific run
events = store.get_events(run_id="run-123")
```

---

## C) Consumers

Each consumer handles a specific side effect domain.

### DiscordConsumer

**File**: `src/events/consumers/discord_consumer.py`

**Responsibilities**:
- Listens to: `notification_discord`, `bets_generated`, `run_finished`
- Formats Discord embeds
- Sends webhook notifications

**Inputs** (events):
```python
# notification_discord
{"title": "Model Retraining Started", "description": "...", "severity": "info"}

# bets_generated
{"run_id": "run-123", "bets": [...]}
```

**Outputs** (side effects):
- POST to Discord webhook URL from environment

### BettingDashboardConsumer

**File**: `src/events/consumers/betting_dashboard_consumer.py`

**Responsibilities**:
- Listens to: `bets_generated`, `bet_settled`
- Maintains betting state projection in `/opt/projects/bootball/data/betting_state.json`
- Tracks pending bets, settled bets, wins/losses, ROI

**Inputs**:
```python
# bets_generated → adds to pending_bets
# bet_settled → moves to settled_bets, updates PnL
```

### HealthDashboardConsumer

**File**: `src/events/consumers/health_dashboard_consumer.py`

**Responsibilities**:
- Listens to: `run_started`, `run_finished`, `health_update`
- Tracks system health metrics
- Computes error rates, average duration, health score
- Writes to `/opt/projects/bootball/data/health_state.json`

### ModelTrendConsumer

**File**: `src/events/consumers/model_trend_consumer.py`

**Responsibilities**:
- Listens to: `model_trend`, `run_finished`
- Tracks model version performance
- Stores calibration drift history
- Writes to `/opt/projects/bootball/data/model_trends.json`

### ModelLifecycleConsumer

**File**: `src/events/consumers/model_lifecycle_consumer.py`

**Responsibilities**:
- Listens to: `model_trend`
- Handles retraining lifecycle events
- Emits Discord notifications for:
  - retraining_started
  - retraining_progress
  - retraining_completed
  - model_version_promoted
  - model_version_rejected

---

## D) State Reconstruction

The StateReconstructor (`src/state/reconstructor.py`) rebuilds system state from events.

### How State is Rebuilt

```python
from src.state.reconstructor import StateReconstructor

reconstructor = StateReconstructor()

# Full replay from events
system = reconstructor.rebuild_from_events()

# Access state
print(system.betting.balance)   # Current balance
print(system.health.health_score)  # System health
print(system.model.market_performance)  # Model metrics
```

### Deterministic Replay Concept

Given the same events, the reconstruction always produces identical state:

1. Load events sorted by timestamp
2. Initialize empty state
3. For each event in order:
   - Apply event handler (e.g., `_handle_bets_generated`)
   - Update state
4. Calculate derived metrics (ROI, etc.)
5. Return final state

### Snapshot Acceleration

Instead of replaying all events every time, snapshots provide incremental reconstruction:

```python
from src.state.reconstructor import StateReconstructor
from src.state.snapshot_store import get_snapshot_store

snapshot_store = get_snapshot_store()

# Get latest snapshot
snapshot = snapshot_store.get_latest_snapshot()

# Incremental rebuild - resume from snapshot
reconstructor = StateReconstructor()
system = reconstructor.rebuild_incremental(events, snapshot)
```

### State Types

#### BettingState
```python
@dataclass
class BettingState:
    balance: float          # Current bankroll
    roi: float              # Return on investment %
    pending_count: int      # Unsettled bets
    wins: int               # Won bets
    losses: int             # Lost bets
    pending_stake: float    # Total staked on pending
    total_pnl: float        # Total profit/loss
    bets: list[dict]        # All bet records
    rounds: list[dict]      # Round history
    active_round_id: int
    active_round_number: int
```

#### HealthState
```python
@dataclass
class HealthState:
    active_runs: list[dict]       # Currently running
    completed_runs: list[dict]    # Historical runs
    health_score: float           # 0-100 score
    error_rate: float             # Errors / total runs
    avg_duration: float           # Average run time
    total_runs: int               # Count
    failed_runs: int              # Failed count
```

#### ModelState
```python
@dataclass
class ModelState:
    model_versions: list[dict]
    market_performance: dict[str, list[dict]]  # By market (h2h, btts, ou25)
    calibration_drift: dict[str, list[dict]]   # ECE over time
    roi_by_model: dict[str, float]
    active_versions: list[str]
    retrain_signals: list[dict]
```

---

## E) Live System (WebSockets)

Real-time dashboard updates use the EventStream.

### Real-time Event Streaming

```python
from src.realtime.event_stream import get_event_stream

stream = get_event_stream()

# Subscribe to events
def handle_event(event):
    print(f"New event: {event['event_type']}")

stream.subscribe(handle_event)

# Push event (from EventBus)
stream.push_event({"event_type": "bets_generated", "payload": {...}})
```

### Dashboard Subscription Model

1. **Initial Load**: Dashboard loads snapshot for fast state
2. **Connect**: WebSocket or poll `/api/events`
3. **Subscribe**: Receive new events as they occur
4. **Update**: Apply events incrementally to state

### Polling Fallback

If WebSocket unavailable:

```bash
# Get events since last known ID
curl "http://localhost:5000/api/events?since_id=5"

# Get recent events
curl "http://localhost:5000/api/events/recent?limit=20"
```

---

## F) Analytics System

The model evaluation system (`src/analytics/`) provides offline replay analytics.

### Model Evaluator

```python
from src.analytics.model_evaluator import ModelEvaluator

evaluator = ModelEvaluator()

# Evaluate last 30 days
result = evaluator.evaluate_by_date_range(days=30)

print(result)
# {
#   "total_bets": 150,
#   "total_pnl": 125.50,
#   "roi": 12.55,
#   "win_rate": 58.0,
#   "market_breakdown": {...},
#   "time_series": [...],
#   "run_stats": {...}
# }
```

### Market Analysis

```python
from src.analytics.market_analysis import MarketAnalyzer

analyzer = MarketAnalyzer()
analysis = analyzer.analyze_markets()

print(analysis)
# {
#   "markets": {
#     "h2h": {"bets": 50, "pnl": 45.0, "roi": 4.5, "win_rate": 56},
#     "btts": {"bets": 40, "pnl": -10.0, "roi": -1.0, "win_rate": 52},
#     "ou25": {"bets": 60, "pnl": 90.0, "roi": 9.0, "win_rate": 62}
#   },
#   "best_market": "ou25",
#   "worst_market": "btts"
# }
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/model-evaluation` | Overall performance |
| `/api/model-evaluation/markets` | Market profitability |
| `/api/model-evaluation/markets/rank` | Ranked markets |
| `/api/model-evaluation/compare` | Compare model versions |

---

## G) Backtesting System

The backtesting system (`src/backtesting/`) enables historical simulation.

### Scenario Simulation

```python
from src.backtesting.backtest_engine import BacktestEngine

config = {
    "min_ev_threshold": 0.05,
    "kelly_multiplier": 0.25,
    "risk_scaling": "balanced",
    "market_filter": None,  # All markets
    "initial_bankroll": 1000.0,
}

engine = BacktestEngine(config)
result = engine.run_backtest()

print(result["roi"])  # +5.2%
```

### Available Scenarios

| Scenario | EV Threshold | Kelly | Risk |
|----------|-------------|-------|------|
| baseline | 0.05 | 0.25 | balanced |
| conservative | 0.10 | 0.15 | conservative |
| aggressive | 0.02 | 0.40 | aggressive |
| h2h_only | 0.05 | 0.25 | balanced |
| btts_only | 0.05 | 0.25 | balanced |

### CLI Usage

```bash
# List scenarios
python -m src.cli.backtest --list-scenarios

# Run baseline
python -m src.cli.backtest --scenario baseline --days 30

# Compare strategies
python -m src.cli.backtest --compare conservative vs aggressive

# Export results
python -m src.cli.backtest --scenario baseline --export-json results.json
```

---

## H) Replay + Audit System

The event replay tool (`src/cli/event_replay.py`) is for debugging and auditing.

### Basic Usage

```bash
# Replay last 100 events
python -m src.cli.event_replay --last 100

# Replay specific run
python -m src.cli.event_replay --run-id run-123

# Replay events from date
python -m src.cli.event_replay --from-date 2025-01-01

# Verbose output
python -m src.cli.event_replay --verbose

# Quiet mode (just results)
python -m src.cli.event_replay --quiet
```

### Export Audit Trail

```bash
# Export complete audit for a run
python -m src.cli.event_replay --run-id run-123 --export

# Export as CSV
python -m src.cli.event_replay --run-id run-123 --export --format csv

# Compare two runs
python -m src.cli.event_replay --run-id run-123 --compare-run run-456
```

### Diff Output

```
=== BETTING COMPARISON ===
  Balance:      1000.00 → 1015.50 (+15.50)
  ROI:          +0.00% → +1.55% (+1.55%)
  Pending:      0 → 0
  Wins/Losses:  0/0 → 2/1
```

---

## I) Drift + Anomaly Detection System

The monitoring system (`src/monitoring/`) provides real-time drift and anomaly detection.

### Drift Detector

```python
from src.monitoring.drift_detector import create_drift_detector
from config.drift_thresholds import get_threshold_config

detector = create_drift_detector(get_threshold_config())

# Analyze event window
results = detector.analyze_event_window(events)

print(f"Health: {results['health_status']}")
for detection in results['detections']:
    print(f"  {detection['type']}: {detection['severity']} (score: {detection['score']:.2f})")
```

### Detection Types

| Type | Description | Triggers |
|------|-------------|----------|
| `model_drift` | Calibration error degradation | ECE increase over window |
| `market_shift` | Market profitability changes | ROI drop per market |
| `roi_anomaly` | Performance collapse/volatility | ROI crash or high volatility |

### Severity Levels

| Severity | Score Range | Action |
|----------|-------------|--------|
| high | 0.8-1.0 | Trigger retraining |
| medium | 0.5-0.79 | Log and monitor |
| low | 0.3-0.49 | Log only |
| none | 0.0-0.29 | No action |

### Configuration

Thresholds are configurable in `config/drift_thresholds.py`:

```python
{
    "drift_alert_threshold": 0.15,
    "roi_drop_threshold": 5.0,
    "volatility_threshold": 2.0,
    "monitoring_time_window_hours": 24,
    "alert_cooldown_seconds": 300,
}
```

### Monitoring Coordinator

The MonitoringCoordinator runs continuous monitoring:

```python
from src.monitoring.monitoring_coordinator import get_monitoring_coordinator

coordinator = get_monitoring_coordinator()
coordinator.start(load_history=True)

# Run analysis on demand
results = coordinator.run_analysis(hours=24)
```

When high-severity drift is detected, it automatically triggers retraining:

```python
# In monitoring_coordinator.py
if severity == "high":
    self._trigger_retraining(detection)
```

---

## J) Automated Retraining System

The system implements a complete closed-loop learning system for self-improvement.

### Lifecycle Manager

The ModelLifecycleManager (`src/models/lifecycle.py`) orchestrates retraining:

```python
from src.models.lifecycle import get_lifecycle_manager

lifecycle = get_lifecycle_manager()

# Evaluate trigger
trigger = lifecycle.evaluate_retrain_trigger(
    drift_report={"detections": [...]},
    performance_report={"roi": -5.0, "calibration_error": 0.12}
)

if trigger["should_retrain"]:
    job_id = lifecycle.start_retraining("h2h", {
        "trigger": "drift",
        "reasons": trigger["reasons"],
        "current_version": "v14"
    })
```

### Trigger Evaluation

Retraining is triggered when:

- **drift_score** > threshold (default: 0.15)
- **ROI degradation** > threshold (default: 3.0%)
- **calibration_error** > threshold (default: 0.10)
- **market instability** detected

### Async Worker

The RetrainWorker (`src/models/retrain_worker.py`) runs training asynchronously:

```python
from src.models.retrain_worker import get_retrain_worker

worker = get_retrain_worker()

# Queue a retraining job (non-blocking)
job_id = worker.queue_retrain("h2h", {
    "trigger": "manual",
    "reasons": ["Testing retrain system"],
    "current_version": "v14"
})

# Job runs in background thread
```

### Training Pipeline

The worker executes:

1. **Load data** - Historical fixtures + predictions (90 days)
2. **Train model** - Calibrated classifier (LogisticRegression + isotonic)
3. **Validate** - Compare vs previous version
4. **Save** - Persist model with version ID
5. **Promote** - Mark as active if improved

### Version Management

Each model version tracks:

```python
version = {
    "id": "v14-h2h-a1b2c3d4",
    "market": "h2h",
    "created_at": "2025-04-25T12:00:00Z",
    "training_data_window": 90,
    "performance_metrics": {
        "accuracy": 0.58,
        "brier_score": 0.22,
    },
    "drift_context": {"trigger": "drift", "score": 0.8},
    "parent_version_id": "v13-h2h",
}
```

### Retraining Events

All retraining stages emit events:

| Event | Description |
|-------|-------------|
| `retraining_started` | Job queued and started |
| `retraining_progress` | Progress updates (10%, 30%, 70%, 90%) |
| `retraining_completed` | Training finished (success/failure) |
| `model_version_promoted` | New version promoted to active |
| `model_version_rejected` | New version rejected (no improvement) |

### User Notifications

The ModelLifecycleConsumer handles notifications:

```python
# Discord notification on completion
"✅ Model v14 promoted (ROI +2.1%)"

# Or rejection
"⚠️ Model v14 deprecated - no improvement over v13"
```

---

## K) Manual Operations Guide

### Replay a Run

```bash
# Debug specific run
python -m src.cli.event_replay --run-id run-abc123 --verbose

# Compare with another
python -m src.cli.event_replay --run-id run-abc123 --compare-run run-def456
```

### Export Audit Logs

```bash
# Full audit export
python -m src.cli.event_replay --run-id run-abc123 --export

# CSV format
python -m src.cli.event_replay --run-id run-abc123 --export --format csv
```

### Run Backtests

```bash
# Run conservative scenario
python -m src.cli.backtest --scenario conservative --days 90

# Compare with baseline
python -m src.cli.backtest --compare baseline vs conservative
```

### Inspect Drift Reports

```python
from src.monitoring.drift_detector import create_drift_detector
from config.drift_thresholds import get_threshold_config
from src.events.event_store import get_event_store

# Load recent events
store = get_event_store()
events = store.get_events(since=datetime(2025, 4, 1))

# Analyze
detector = create_drift_detector(get_threshold_config())
results = detector.analyze_event_window(events)

for d in results['detections']:
    print(f"{d['type']}: {d['severity']} (score: {d['score']:.2f})")
```

### Inspect Model Versions

```python
from src.models.model_tracker import get_model_tracker

tracker = get_model_tracker()
versions = tracker.get_versions("h2h")

for v in versions:
    print(f"{v['version_number']}: brier={v['brier_score']}, active={v['is_active']}")
```

### Manually Trigger Retraining

```python
from src.models.retrain_worker import get_retrain_worker

worker = get_retrain_worker()
job_id = worker.queue_retrain("h2h", {
    "trigger": "manual",
    "reasons": ["User requested retrain"],
    "current_version": "v14"
})
print(f"Queued job: {job_id}")
```

### Rebuild State

```python
from src.state.reconstructor import StateReconstructor
from src.state.snapshot_writer import save_run_snapshot

# Rebuild from events
reconstructor = StateReconstructor()
system = reconstructor.rebuild_from_events()

# Save snapshot
snapshot = save_run_snapshot(run_id="manual", is_complete=True)
print(f"Saved snapshot {snapshot.id}")
```

### Check Dashboard State

```python
from src.state.builders.betting_state_builder import build_betting_state
from src.state.builders.health_state_builder import build_health_state

# Betting state
betting = build_betting_state()
print(f"Balance: {betting.balance}")

# Health state  
health = build_health_state()
print(f"Health: {health.health_score}")
```

---

## L) Daily Pipeline Reference

### What It Does

The daily pipeline (`scripts/daily_run.py`) is the core execution engine:

1. **Fetches fixtures** - Retrieves upcoming matches from api-football
2. **Fetches odds** - Gets latest bookmaker odds
3. **Generates predictions** - Runs ML models for each fixture
4. **Detects value bets** - Identifies positive EV opportunities using Kelly criterion
5. **Emits events** - Publishes structured events for consumption

### What It Does NOT Do

Critically, the daily pipeline:

- ❌ Does NOT send Discord alerts
- ❌ Does NOT format messages
- ❌ Does NOT update dashboards directly
- ❌ Does NOT compute UI state

All side effects are delegated to consumers.

### Event Emission

```python
# At pipeline start
EventBus.emit(Events.RUN_STARTED, {
    "run_id": run_id,
    "mode": "daily",
    "timestamp": now.isoformat(),
})

# After predictions
EventBus.emit(Events.PREDICTIONS_GENERATED, {
    "run_id": run_id,
    "fixture_count": self.fixture_count,
    "prediction_count": self.prediction_count,
})

# When value bets found
EventBus.emit(Events.BETS_GENERATED, {
    "run_id": run_id,
    "bets": [...]  # Array of bet objects
})

# At completion
EventBus.emit(Events.RUN_FINISHED, {
    "run_id": run_id,
    "mode": "daily",
    "total_bets": len(self.value_bets),
    "total_ev": sum(b["ev"] for b in self.value_bets),
    "errors": self.errors,
    "duration": duration,
})
```

### Lifecycle

```
1. Initialize → create DailyPipeline instance
2. run() method:
   a. _fetch_completed() → settlement input
   b. _fetch_upcoming() → get next 7 days of fixtures
   c. _process_fixture() → for each fixture, fetch odds
   d. _run_predictions() → ML inference
   e. emit events → EventBus publishes
3. Return → Pipeline complete
```