# Bootball - Football Prediction Platform

Bootball is a full-stack football prediction and simulated betting intelligence platform. It combines machine learning models with an event-driven architecture to generate value bets and track performance.

## Project Description

Bootball uses historical match data, odds, and machine learning to identify positive expected value (EV) betting opportunities. The system:

1. Ingests fixture and odds data from api-football
2. Generates calibrated probability predictions using ML ensembles
3. Identifies value bets where our probability differs from bookmaker odds
4. Simulates bet placement using Kelly criterion sizing (fake money)
5. Tracks performance, ROI, and model health

### Why Event-Driven Architecture?

The system was built with an event-driven architecture for several reasons:

1. **Determinism**: Any system state can be reconstructed by replaying events
2. **Auditability**: Every decision is traceable through immutable event history
3. **Separation of Concerns**: Pipelines emit events; consumers handle side effects
4. **Extensibility**: New consumers can be added without modifying core logic
5. **Testability**: Each component can be tested in isolation

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           PIPELINE                                   │
│  scripts/daily_run.py                                               │
│       │                                                             │
│       ├─► Fetch Fixtures + Odds                                      │
│       ├─► Run ML Predictions                                         │
│       ├─► Detect Value Bets                                          │
│       └─► EventBus.emit() ────────────────────────────────────────► │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         EVENT SYSTEM                                  │
│  src/alerts/event_bus.py                                             │
│       │                                                             │
│       ├─► Log to in-memory buffer                                    │
│       ├─► Dispatch to consumers                                      │
│       └─► Persist to EventStore ──────────────────────────────────►  │
│                                                                     │
│  src/events/event_store.py (events.jsonl)                           │
│       │                                                             │
│       │  Immutable append-only log                                   │
│       │                                                             │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          CONSUMERS                                    │
│       │                        │                    │                  │
│       ▼                        ▼                    ▼                  │
│  DiscordConsumer     BettingDashboardConsumer  HealthDashboardConsumer│
│  (alerts)            (dashboard state)         (health metrics)      │
│                                                                      │
│             ◄──────────────────────────────────┘                   │
│                         │                                            │
│                         ▼                                            │
│              ModelTrendConsumer                                      │
│              (model tracking)                                         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      DASHBOARDS                                       │
│       │                        │                    │                  │
│       ▼                        ▼                    ▼                  │
│  Betting Dashboard      Health Dashboard      Model Evaluation       │
│  (real-time)           (real-time)           (offline analytics)    │
└─────────────────────────────────────────────────────────────────────┘
```

### Alternative Flows

```
EventStore ──────────────────────────────────────────────────────────►
    │                                                                 │
    ├──► StateReconstructor ──► Snapshots ──► Dashboards (fast init) │
    │                                                                 │
    ├──► Replay CLI ──────────────────────────────────────────► Debug│
    │                                                                 │
    ├──► Model Evaluator ──────────────────────────────────────► Analytics│
    │                                                                 │
    └──► BacktestEngine ──────────────────────────────────────► Simulation│
```

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

### Analytics System

| Module | File | Purpose |
|--------|------|---------|
| ModelEvaluator | `src/analytics/model_evaluator.py` | Performance analysis |
| MarketAnalyzer | `src/analytics/market_analysis.py` | Market profitability |
| ModelComparator | `src/analytics/model_comparator.py` | Model A/B testing |

### Backtesting System

| Module | File | Purpose |
|--------|------|---------|
| BacktestEngine | `src/backtesting/backtest_engine.py` | Historical simulation |
| Scenarios | `src/backtesting/scenarios.py` | Strategy configurations |
| Comparator | `src/backtesting/comparator.py` | Strategy comparison |

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

### Start Dashboard

```bash
# Start Flask app (includes scheduler)
python backend/app.py

# Or directly
python scripts/web_ui.py
```

Dashboard available at: http://localhost:5000

### Run Replay CLI

```bash
# Last 100 events
python -m src.cli.event_replay --last 100

# Specific run
python -m src.cli.event_replay --run-id run-abc123

# Compare runs
python -m src.cli.event_replay --run-id run-abc --compare-run run-def
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

## Key Design Principles

### 1. Events as Source of Truth

All system state changes are captured as immutable events. The current state is always a derived view reconstructed from events.

```python
# Bad: Pipeline directly updates dashboard state
dashboard.update(betting_balance)

# Good: Pipeline emits event
EventBus.emit(Events.BETS_GENERATED, {"bets": [...], "run_id": "..."})
```

### 2. Immutability

Events are never modified or deleted. This enables:
- Deterministic replay
- Full audit trail
- Point-in-time reconstruction

```python
# Events are append-only
with open("events.jsonl", "a") as f:
    f.write(json.dumps(event) + "\n")
```

### 3. Deterministic Replay

Given the same events, state reconstruction always produces identical results:

```python
# Always produces same state from same events
def rebuild(events):
    events = sorted(events, key=lambda e: e["timestamp"])
    state = initial_state()
    for event in events:
        apply_event(state, event)
    return state
```

### 4. Snapshot Acceleration

Rather than replaying all events, snapshots provide incremental reconstruction:

```
Full replay:  10,000 events → 5 seconds
With snapshot: 500 events (since snapshot) → 0.1 seconds
```

### 5. Separation of Concerns

Pipelines do NOT:
- Send alerts
- Update dashboards
- Format messages
- Know about consumers

Consumers do NOT:
- Run ML models
- Access api-football
- Make betting decisions

## Examples

### Sample Event Flow

```bash
# 1. Pipeline starts
[RUN_STARTED] run_id=run-20250425-001, mode=daily

# 2. Predictions generated
[PREDICTIONS_GENERATED] fixture_count=15, prediction_count=45

# 3. Value bets identified
[BETS_GENERATED] +5 bets (EV: 6.5%, 8.2%, 5.1%, 7.0%, 5.9%)

# 4. Match settles
[BET_SETTLED] 3 settled, PnL: +12.40, W/L: 2/1

# 5. Pipeline completes
[RUN_FINISHED] bets=5, total_ev=0.38, duration=45.2s
```

### Sample Backtest Run

```bash
$ python -m src.cli.backtest --scenario conservative --days 30

Running backtest: conservative
Days: 30
============================================================
BACKTEST RESULTS
============================================================
  Total Bets:     42
  Total PnL:      +35.20
  ROI:            +3.52%
  Win Rate:       58.0%
  Wins:           24 / 18
  Avg Stake:      8.50

  Max Bankroll:   1035.20
  Min Bankroll:   985.00
  Max Drawdown:   5.2%
```

### Sample Replay Command

```bash
$ python -m src.cli.event_replay --run-id run-20250425-001 --verbose

Loading events...
Loaded 8 events

Replaying events...

[1/8] run_started: run_id=run-2025, mode=daily
[2/8] predictions_generated: 15 fixtures, 45 predictions
[3/8] bets_generated: +5 bets (run=run-2025)
       - h2h: H @ 2.1 (EV: 6.00%)
       - btts: yes @ 1.9 (EV: 5.00%)
       - ou25: over @ 2.0 (EV: 8.00%)
[4/8] run_finished: run_id=run-2025, mode=daily, bets=5, duration=45.2s

==================================================
FINAL RECONSTRUCTED STATE
==================================================
=== BETTING STATE ===
  Balance:     1,012.40
  ROI:         +1.24%
  Pending:     2 bets (15.00)

=== HEALTH STATE ===
  Health Score:    95.0
  Error Rate:      5.00%
  Avg Duration:   45.2s
```

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
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3, Flask |
| Database | SQLite3 (WAL mode) |
| ML | XGBoost, LightGBM, scikit-learn |
| Scheduling | APScheduler |
| Frontend | Flask templates |
| API | api-football (RapidAPI) |

## License

MIT License - See LICENSE file for details.

## Support

For issues or questions:
- Report bugs: https://github.com/anomalyco/bootball/issues
- Documentation: This manual and docs/ directory