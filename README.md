# Bootball - Football Prediction Platform

Bootball is an **event-driven autonomous betting intelligence platform with closed-loop learning and deterministic replay capability**.

## Project Description

Bootball combines machine learning models with a fully event-sourced architecture to generate value bets, track performance, detect model drift, and automatically improve through self-training.

### Core Capabilities

1. **Event-Driven Execution**: Pipelines emit immutable events; consumers handle all side effects
2. **Deterministic State**: Any system state can be reconstructed by replaying events
3. **Drift Detection**: Real-time monitoring for model performance degradation
4. **Automated Retraining**: Self-improving model lifecycle with version control
5. **Full Auditability**: Every decision traceable through immutable event history

---

## Full System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXECUTION LAYER                                   │
│  scripts/daily_run.py                                                       │
│       │                                                                     │
│       ├─► Fetch Fixtures + Odds                                             │
│       ├─► Run ML Predictions                                                │
│       ├─► Detect Value Bets                                                  │
│       └─► EventBus.emit() ──────────────────────────────────────────────► │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EVENT LAYER (CORE TRUTH)                            │
│  src/alerts/event_bus.py                                                    │
│       │                                                                     │
│       ├─► Log to in-memory buffer                                           │
│       ├─► Dispatch to consumers                                             │
│       └─► Persist to EventStore ──────────────────────────────────────────►  │
│                                                                     │        │
│  src/events/event_store.py (events.jsonl)                      │        │
│       │                                                        │        │
│       │  Immutable append-only log                              │        │
│       │                                                        │        │
│  Canonical Event Types:                                         │        │
│  - bets_generated, bet_settled                                    │        │
│  - run_started, run_finished, predictions_generated               │        │
│  - health_update, model_trend                                    │        │
│  - drift_detected, market_shift, roi_anomaly                     │        │
│  - retraining_started, retraining_progress, retraining_completed │        │
│  - model_version_promoted, model_version_rejected                │        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           STATE LAYER                                       │
│  src/state/reconstructor.py                                                 │
│       │                                                                     │
│       ├─► BettingState (balance, ROI, bets, rounds)                        │
│       ├─► ModelState (versions, performance, calibration)                     │
│       ├─► HealthState (error rate, duration, health score)                  │
│       └─► Snapshot System (incremental reconstruction)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CONSUMERS LAYER                                     │
│       │                        │                    │                       │
│       ▼                        ▼                    ▼                       │
│  DiscordConsumer     BettingDashboardConsumer  HealthDashboardConsumer      │
│  (alerts)            (dashboard state)         (health metrics)            │
│                                                                      │
│       │                        │                    │                       │
│       ▼                        ▼                    ▼                       │
│  ModelTrendConsumer    ModelLifecycleConsumer   MonitoringAlerts            │
│  (model tracking)     (retraining events)       (drift alerts)             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ANALYTICS LAYER                                    │
│  src/analytics/                                                             │
│       │                                                                     │
│       ├─► model_evaluator.py - offline replay analytics                    │
│       ├─► market_analysis.py - market profitability                         │
│       ├─► model_comparator.py - model A/B testing                           │
│       └─► audit_exporter.py - audit trail export                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SIMULATION LAYER                                     │
│  src/backtesting/                                                           │
│       │                                                                     │
│       ├─► backtest_engine.py - historical simulation                       │
│       ├─► scenarios.py - strategy configurations                            │
│       └─► comparator.py - strategy comparison                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MONITORING LAYER                                    │
│  src/monitoring/                                                            │
│       │                                                                     │
│       ├─► drift_detector.py - model drift & anomaly detection              │
│       ├─► window_processor.py - rolling event windows                       │
│       └─► monitoring_coordinator.py - continuous monitoring                │
│                                                                            │
│  Detection Types:                                                           │
│  - model_drift: Calibration error degradation                              │
│  - market_shift: Market profitability changes                              │
│  - roi_anomaly: Performance collapse/volatility spikes                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          LEARNING LAYER (NEW)                               │
│  src/models/                                                                │
│       │                                                                     │
│       ├─► lifecycle.py - ModelLifecycleManager                              │
│       │       - evaluate_retrain_trigger()                                  │
│       │       - start_retraining()                                          │
│       │       - finalize_retraining()                                       │
│       │       - promote_version()                                           │
│       │                                                                     │
│       └─► retrain_worker.py - Async training worker                        │
│               - queue_retrain()                                             │
│               - _train_model()                                             │
│               - _validate_against_previous()                               │
│               - _save_model()                                               │
│                                                                            │
│  Retraining Triggers:                                                        │
│  - drift_score > threshold                                                  │
│  - ROI degradation over rolling window                                      │
│  - calibration_error > threshold                                            │
│  - market instability detected                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            UI LAYER                                          │
│  Flask + WebSocket                                                           │
│       │                                                                     │
│       ├─► Live Dashboard (real-time)                                        │
│       ├─► Model Evaluation Dashboard (offline analytics)                   │
│       ├─► Health Dashboard (system metrics)                                 │
│       └─► WebSocket subscriptions                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Alternative Flows

```
EventStore ──────────────────────────────────────────────────────────────►
    │                                                                     │
    ├──► StateReconstructor ──► Snapshots ──► Dashboards (fast init)     │
    │                                                                     │
    ├──► Replay CLI ──────────────────────────────────────────────► Debug│
    │                                                                     │
    ├──► Model Evaluator ──────────────────────────────────────► Analytics│
    │                                                                     │
    ├──► BacktestEngine ──────────────────────────────────────► Simulation│
    │                                                                     │
    └──► MonitoringCoordinator ──► Drift Detection ──► Retraining       │
```

---

## Closed-Loop Learning System

The system implements a complete feedback loop for self-improvement:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Event       │ ──► │  Drift       │ ──► │  Retraining  │
│  Stream      │     │  Detection   │     │  Trigger     │
└──────────────┘     └──────────────┘     └──────────────┘
                                               │
                                               ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  New         │ ◄── │  Model       │ ◄── │  Version     │
│  Predictions │     │  Training    │     │  Evaluation  │
└──────────────┘     └──────────────┘     └──────────────┘
       │                                           │
       └──────────────────┬────────────────────────┘
                          ▼
              ┌──────────────────────┐
              │  User Notification  │
              │  (Discord + Dashboard)│
              └──────────────────────┘
```

### Lifecycle Events

1. **MonitoringCoordinator** analyzes event windows for drift/market shifts/ROI anomalies
2. **DriftDetector** emits high-severity alerts when thresholds exceeded
3. **MonitoringCoordinator._trigger_retraining()** evaluates retrain trigger
4. **ModelLifecycleManager** starts async retraining job
5. **RetrainWorker** loads data, trains model, validates against previous version
6. **ModelLifecycleManager** emits completion event with metrics
7. **ModelLifecycleConsumer** notifies user via Discord
8. New model version is now active for predictions

---

## Key System Properties

| Property | Description |
|----------|-------------|
| **Deterministic Replay** | Any state can be reconstructed from events - identical every time |
| **Event-Sourced** | All state changes captured as immutable events |
| **No Hidden State** | Every piece of state derived from events |
| **Full Auditability** | Every decision traceable through event history |
| **Reproducible Backtests** | Historical simulation with model swapping |
| **Self-Improving** | Automated model retraining on drift detection |

---

## Quick Start

### Run Daily Pipeline

```bash
# Run with default leagues
python scripts/daily_run.py

# Run specific leagues
python scripts/daily_run.py --leagues 1,2,3

# Dry run (no predictions saved)
python scripts/daily_run.py --dry-run
```

### Run Replay CLI

```bash
# Last 100 events
python -m src.cli.event_replay --last 100

# Specific run
python -m src.cli.event_replay --run-id run-abc123

# Compare runs
python -m src.cli.event_replay --run-id run-abc --compare-run run-def

# Export audit trail
python -m src.cli.event_replay --run-id run-abc --export
```

### Run Backtests

```bash
# List scenarios
python -m src.cli.backtest --list-scenarios

# Run scenario
python -m src.cli.backtest --scenario baseline --days 30

# Compare strategies
python -m src.cli.backtest --compare conservative vs aggressive
```

### View Live Dashboard

```bash
# Start Flask app (includes scheduler)
python backend/app.py

# Or directly
python scripts/web_ui.py
```

Dashboard available at: http://localhost:5000

### How Retraining Triggers Work

Retraining is triggered automatically when:

```python
# In src/monitoring/monitoring_coordinator.py
if severity == "high":
    lifecycle = get_lifecycle_manager()
    trigger = lifecycle.evaluate_retrain_trigger(drift_report, performance_report)
    
    if trigger["should_retrain"]:
        worker = get_retrain_worker()
        worker.queue_retrain(market, context)
```

### Inspect Drift Reports

```python
from src.monitoring.drift_detector import create_drift_detector
from config.drift_thresholds import get_threshold_config

detector = create_drift_detector(get_threshold_config())
results = detector.analyze_event_window(events)

print(f"Health: {results['health_status']}")
for d in results['detections']:
    print(f"  {d['type']}: {d['severity']} (score: {d['score']:.2f})")
```

---

## Core Modules

### Betting Pipeline

| Module | File | Purpose |
|--------|------|---------|
| daily_run | `scripts/daily_run.py` | Main pipeline orchestrator |
| make_predictions | `scripts/make_predictions.py` | ML prediction generation |
| auto_bet | `scripts/auto_bet.py` | Bet placement bot |
| settle_fixtures | `scripts/settle_fixtures.py` | Post-match settlement |

### Event System

| Module | File | Purpose |
|--------|------|---------|
| EventBus | `src/alerts/event_bus.py` | Central event dispatcher |
| EventStore | `src/events/event_store.py` | Immutable event persistence |
| Consumers | `src/events/consumers/` | Side effect handlers |
| Routing | `src/events/routing.py` | Event-to-consumer mapping |

### State System

| Module | File | Purpose |
|--------|------|---------|
| Reconstructor | `src/state/reconstructor.py` | Rebuild state from events |
| Snapshots | `src/state/snapshots.py` | Snapshot data model |
| SnapshotStore | `src/state/snapshot_store.py` | Snapshot persistence |
| Builders | `src/state/builders/` | Dashboard state builders |

### Monitoring & Learning

| Module | File | Purpose |
|--------|------|---------|
| DriftDetector | `src/monitoring/drift_detector.py` | Model drift detection |
| MonitoringCoordinator | `src/monitoring/monitoring_coordinator.py` | Continuous monitoring |
| ModelLifecycleManager | `src/models/lifecycle.py` | Retraining orchestration |
| RetrainWorker | `src/models/retrain_worker.py` | Async model training |

### Analytics & Simulation

| Module | File | Purpose |
|--------|------|---------|
| ModelEvaluator | `src/analytics/model_evaluator.py` | Performance analysis |
| MarketAnalyzer | `src/analytics/market_analysis.py` | Market profitability |
| ModelComparator | `src/analytics/model_comparator.py` | Model A/B testing |
| BacktestEngine | `src/backtesting/backtest_engine.py` | Historical simulation |

---

## Configuration

Key environment variables:

```bash
# API
API_FOOTBALL_KEY=your-rapidapi-key

# Database
DATABASE_PATH=data/football.db
SCHEDULER_DB_PATH=data/scheduler.db

# Betting
BOT_ENABLED=false
BOT_MIN_EV=0.05
BOT_MAX_STAKE=100.0

# Auth
SECRET_KEY=random-secret-key
BOOTBALL_PASSWORD=changeme

# Alerts (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Drift Detection
DRIFT_ALERT_THRESHOLD=0.15
ROI_DROP_THRESHOLD=5.0
```

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3, Flask |
| Database | SQLite3 (WAL mode) |
| ML | XGBoost, LightGBM, scikit-learn |
| Scheduling | APScheduler |
| Frontend | Flask templates |
| API | api-football (RapidAPI) |

---

## License

MIT License - See LICENSE file for details.