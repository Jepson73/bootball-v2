# Bootball User Manual

This is the comprehensive technical manual for the Bootball betting intelligence system. It describes the complete architecture, event system, and operational procedures.

## A) System Overview

Bootball is a full-stack football prediction and simulated betting intelligence platform. It combines machine learning models with an event-driven architecture to generate value bets and track performance.

### Architecture Summary

The system is built on an **event-driven architecture** where:

1. **Events are the single source of truth** - All state changes are captured as immutable events
2. **Pipelines emit events, they do not mutate state directly** - The daily pipeline generates predictions and bets, then emits events describing what happened
3. **Consumers handle all side effects** - Discord alerts, dashboard updates, and health monitoring are handled by separate consumers that subscribe to events
4. **State is reconstructed from events** - Any point in time can be recreated by replaying events from the beginning

### Core Philosophy

The fundamental principle is **immutable event history**:

```
Pipeline (daily_run) → Events → State Reconstruction
                            ↓
                       Consumers
                            ↓
                       Dashboards
```

This design provides:
- **Determinism**: Replaying events always produces identical state
- **Auditability**: Every decision is traceable through event history
- **Testability**: Consumers can be tested in isolation
- **Extensibility**: New consumers can be added without modifying pipelines

## B) Event System Explanation

### What Events Exist

The system uses a canonical set of event types defined in `src/alerts/event_bus.py`:

| Event Type | Description | Payload |
|------------|--------------|---------|
| `run_started` | Pipeline execution begins | `run_id`, `mode`, `timestamp` |
| `run_finished` | Pipeline execution completes | `run_id`, `mode`, `total_bets`, `total_ev`, `errors`, `duration` |
| `bets_generated` | Value bets identified | `run_id`, `bets` array with fixture_id, market, outcome, odds, ev, stake |
| `bet_settled` | Bets resolved after match | `run_id`, `settled_count`, `pnl_total`, `wins`, `losses` |
| `model_trend` | Model metrics updated | `market`, `model_version`, `brier_score`, `ece`, `accuracy` |
| `health_update` | System health snapshot | `health_score`, `error_rate` |
| `predictions_generated` | ML predictions made | `run_id`, `fixture_count`, `prediction_count` |

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
            └─► Consumers (side effects)                  │
                    │                                      │
                    ├─► DiscordConsumer (alerts)            │
                    ├─► BettingDashboardConsumer            │
                    ├─► HealthDashboardConsumer             │
                    └─► ModelTrendConsumer                 │
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

## C) Daily Pipeline (daily_run)

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
- ❌ Does NOT call BettingAlerts or DiscordAlerts

All side effects are delegated to consumers.

### How It Emits Events

```python
# In scripts/daily_run.py
from src.alerts.event_bus import event_bus as EventBus, Events

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

## D) Consumers

Each consumer handles a specific side effect domain.

### DiscordConsumer

**File**: `src/events/consumers/discord_consumer.py`

**Responsibilities**:
- Listens to: `bets_generated`, `run_finished`
- Formats Discord embeds
- Sends webhook notifications

**Inputs** (events):
```python
# bets_generated
{"run_id": "run-123", "bets": [...]}

# run_finished  
{"run_id": "run-123", "mode": "daily", "total_bets": 5, "errors": []}
```

**Outputs** (side effects):
- POST to Discord webhook URL from environment

**Configuration**:
```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

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

**Outputs** (side effects):
- JSON file state updates

### HealthDashboardConsumer

**File**: `src/events/consumers/health_dashboard_consumer.py`

**Responsibilities**:
- Listens to: `run_started`, `run_finished`, `health_update`
- Tracks system health metrics
- Computes error rates, average duration, health score
- Writes to `/opt/projects/bootball/data/health_state.json`

**Inputs**:
```python
# run_started → register active run
# run_finished → update completed runs, calculate metrics
# health_update → direct health score update
```

**Outputs**:
- JSON file with health metrics

### ModelTrendConsumer

**File**: `src/events/consumers/model_trend_consumer.py`

**Responsibilities**:
- Listens to: `model_trend`, `run_finished`
- Tracks model version performance
- Stores calibration drift history
- Writes to `/opt/projects/bootball/data/model_trends.json`

**Inputs**:
```python
# model_trend → update market performance, calibration drift
# run_finished → track completed runs per mode
```

**Outputs**:
- JSON file with model metrics

## E) State Reconstruction

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

### BettingState

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

### HealthState

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

### ModelState

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

## F) Replay CLI

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

### Debugging Workflows

**Inspect a specific run:**
```bash
python -m src.cli.event_replay --run-id run-123 --verbose
```

**Compare two runs:**
```bash
python -m src.cli.event_replay --run-id run-123 --compare-run run-456
```

**Filter by event type:**
```bash
python -m src.cli.event_replay --event-types run_started,run_finished
```

**Diff mode output:**
```
=== BETTING COMPARISON ===
  Balance:      1000.00 → 1015.50 (+15.50)
  ROI:          +0.00% → +1.55% (+1.55%)
  Pending:      0 → 0
  Wins/Losses:  0/0 → 2/1
```

## G) Live System (WebSockets)

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

### Flow

```
EventBus.emit()
    → push to EventStream (in-memory)
    → WebSocket broadcast (if SocketIO available)
    → Polling endpoint /api/events

Dashboard
    → Load snapshot (fast init)
    → Poll /api/events?since_id=X (incremental updates)
    → Update UI
```

## H) Model Evaluation Dashboard

The model evaluation system (`src/analytics/`) provides offline replay analytics.

### How It Works

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

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/model-evaluation` | Overall performance |
| `/api/model-evaluation/markets` | Market profitability |
| `/api/model-evaluation/markets/rank` | Ranked markets |
| `/api/model-evaluation/compare` | Compare model versions |
| `/api/model-evaluation/optimal` | Find best model |

### ROI Calculation

ROI is computed from events:

```
ROI = (Total PnL / Initial Bankroll) × 100

Initial Bankroll = 1000 SEK (configurable)
```

### Market Performance Breakdown

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

## I) Backtesting Engine

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

### Predefined Scenarios

```python
from src.backtesting.scenarios import SCENARIOS, run_scenario

# Conservative strategy
result = run_scenario("conservative", days=30)

# Aggressive strategy  
result = run_scenario("aggressive", days=30)

# Compare
from src.backtesting.comparator import compare_scenarios
comparison = compare_scenarios("baseline", "aggressive", days=30)
```

### Available Scenarios

| Scenario | EV Threshold | Kelly | Risk |
|----------|-------------|-------|------|
| baseline | 0.05 | 0.25 | balanced |
| conservative | 0.10 | 0.15 | conservative |
| aggressive | 0.02 | 0.40 | aggressive |
| h2h_only | 0.05 | 0.25 | balanced |
| btts_only | 0.05 | 0.25 | balanced |

### Model Override Logic

The backtest engine can simulate different model versions:

```python
config = {
    "model_version_override": "v14",
    "min_ev_threshold": 0.05,
}

engine = BacktestEngine(config)
result = engine.run_backtest()
```

This replays historical events but substitutes model version decisions.

### Risk Parameters

| Parameter | Description | Range |
|-----------|-------------|-------|
| `kelly_multiplier` | Fraction of Kelly to use | 0.1 - 1.0 |
| `min_ev_threshold` | Minimum EV to bet | 0.02 - 0.15 |
| `risk_scaling` | Risk multiplier | conservative/balanced/aggressive |
| `stop_loss_pct` | Stop if drawdown exceeds | 0.1 - 0.5 |
| `max_stake_pct` | Max stake as % of bankroll | 0.05 - 0.25 |

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

### Example: Conservative vs Aggressive

```bash
$ python -m src.cli.backtest --compare conservative vs aggressive

SCENARIO COMPARISON
============================================================

  conservative:
    ROI: +2.1%
    PnL: +21.00
    Bets: 45

  aggressive:
    ROI: +8.5%
    PnL: +85.00
    Bets: 120

------------------------------------------------------------
  DELTAS:
    ROI Delta:     +6.4%
    PnL Delta:     +64.00
    Bets Delta:    +75
    Drawdown Delta: +12.3%

  WINNER: aggressive (+6.40%)
```

## J) Manual Operations Guide

### Replay a Run

```bash
# Debug specific run
python -m src.cli.event_replay --run-id run-abc123 --verbose

# Compare with another
python -m src.cli.event_replay --run-id run-abc123 --compare-run run-def456
```

### Inspect Events

```bash
# Last 50 events
python -m src.cli.event_replay --last 50 --quiet

# By date range
python -m src.cli.event_replay --from-date 2025-01-01 --to-date 2025-01-31
```

### Rebuild Snapshot

```python
from src.state.snapshot_writer import save_run_snapshot
from src.state.reconstructor import StateReconstructor

# Rebuild from events
reconstructor = StateReconstructor()
system = reconstructor.rebuild_from_events()

# Save snapshot
snapshot = save_run_snapshot(run_id="manual", is_complete=True)
print(f"Saved snapshot {snapshot.id}")
```

### Run Backtest Scenario

```bash
# Run conservative scenario
python -m src.cli.backtest --scenario conservative --days 90

# Compare with baseline
python -m src.cli.backtest --compare baseline vs conservative
```

### Inspect Model Performance

```python
from src.analytics.model_evaluator import evaluate_model_performance
from src.analytics.market_analysis import rank_markets_by_profitability

# Overall performance
result = evaluate_model_performance(days=30)
print(f"ROI: {result['roi']}%")

# Market ranking
ranking = rank_markets_by_profitability(days=30)
for r in ranking:
    print(f"{r['market']}: {r['roi']}%")
```

### Query Event Store Directly

```python
from src.events.event_store import get_event_store

store = get_event_store()

# Count events
print(f"Total events: {store.count()}")

# Get specific run
events = store.get_events(run_id="run-abc")
for e in events:
    print(e['event_type'], e.get('timestamp'))
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