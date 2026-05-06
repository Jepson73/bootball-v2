# Bootball User Manual

This is the comprehensive technical manual for the Bootball betting intelligence platform. It describes the complete architecture, operational procedures, and the automated self-improvement system.

## Table of Contents

A) System Overview
B) Event System
C) Pipeline Architecture
D) Prediction Identity & Persistence
E) Portfolio-First Capital Allocation
F) Closed-Loop Validation (CLVE)
G) Execution Runtime
H) Observability & Monitoring
I) Configuration
J) Troubleshooting

---

## A) System Overview

Bootball is an **event-driven autonomous betting intelligence platform with portfolio-first capital allocation, closed-loop learning, and unified observability**.

### Architecture Summary

The system is built on **portfolio-first architecture** where:

1. **UnifiedPredictionService** is the single source of truth for predictions
2. **PortfolioEngine** allocates capital across all markets (h2h, btts, ou25, ou15)
3. **ExecutionEngine** places bets using Kelly criterion sizing
4. **LineageTracker** tracks runs end-to-end with immutable JSON files
5. **CLVE** validates system adaptation before each execution

### Core Philosophy

The fundamental principle is **deterministic, observable, self-improving prediction pipeline**:

```
ExecutionRuntime
       │
       ▼
UnifiedPredictionService ───► prediction_id + odds_snapshot
       │                              │
       ▼                              ▼
Prediction Records ──────────► is_legacy=0 (immutable)
       │
       ▼
PortfolioEngine ────────────► Kelly sizing + Markowitz optimization
       │
       ▼
PolicyEngine ──────────────► Constraints (max exposure, min odds)
       │
       ▼
CLVE ──────────────────────► PDS/AI/RR/PS/CDS validation
       │                              │
       │                    BLOCKS if failing
       ▼
ExecutionEngine ───────────► Bet placement + settlement
       │
       ▼
LineageTracker ───────────► JSON persistence
       │
       ▼
SystemTruth ───────────────► /system/truth API
```

This design provides:
- **Determinism**: Runs produce identical results with same inputs
- **Auditability**: Every prediction traced via lineage
- **Observability**: Single API for all dashboard data
- **Self-Improvement**: CLVE blocks execution when not adapting

---

## B) Event System

The canonical event types are defined in `src/alerts/event_bus.py`:

### Betting Events

| Event | Description | Key Payload |
|-------|--------------|-------------|
| `PREDICTIONS_GENERATED` | New predictions ready | count, timestamp |
| `BET_PLACED` | Bet submitted to ExecutionEngine | bet_id, fixture, market, stake |
| `BET_SETTLED` | Match completed | bet_id, won, pnl |
| `RUN_COMPLETED` | Pipeline cycle finished | run_id, predictions, bets |

### System Events

| Event | Description | Key Payload |
|-------|--------------|-------------|
| `CLVE_EVALUATED` | Validation complete | decision, scores |
| `DRIFT_DETECTED` | Model drift alert | market, drift_score |
| `MODEL_RETRAINED` | New model version | market, version_id |
| `GOVERNANCE_ALERT` | Policy violation | constraint, details |

### Consumer Pattern

Events flow to consumers that handle side effects:

- **DiscordConsumer**: Sends alerts
- **BettingDashboardConsumer**: Updates web state
- **HealthDashboardConsumer**: Updates health metrics
- **ModelTrendConsumer**: Tracks model performance
- **CalibrationConsumer**: Updates calibration curves

---

## C) Pipeline Architecture

### Execution Runtime Process

The `ExecutionRuntime` runs as an independent process (`backend/runtime/execution_runtime.py`):

```python
class ExecutionRuntime:
    def execute_cycle(self):
        run_id = generate_uuid()
        
        # 1. Start lineage
        lineage_tracker.start_lineage(run_id)
        
        # 2. Run coordinator
        result = coordinator.run_cycle()
        
        # 3. Complete lineage
        lineage_tracker.complete_lineage("COMPLETE")
```

### Agent Coordinator

The `AgentCoordinator` orchestrates multi-agent pipeline:

1. **Predictor Agent**: Generates predictions via UnifiedPredictionService
2. **Risk Manager Agent**: Evaluates portfolio risk, sets λ (risk aversion)
3. **Execution Strategist Agent**: Creates candidate portfolio
4. **Adversary Agent**: Stress-tests portfolio for vulnerabilities
5. **Learning System**: Evaluates performance, updates weights

### Run Lifecycle

```
RUN_START → PREDICTION → RISK → PORTFOLIO → EXECUTION → LEARNING → RUN_END
              │            │         │            │           │
              ▼            ▼         ▼            ▼           ▼
         save_preds   eval_risk  allocate    place_bets   evaluate
```

---

## D) Prediction Identity & Persistence

### UnifiedPredictionService

Single source of truth for all predictions (`src/prediction/unified_prediction_service.py`):

```python
class UnifiedPredictionService:
    def generate_with_fixture_data(self, fixtures):
        # Generate predictions with UUID
        predictions = [{
            "prediction_id": str(uuid.uuid4()),
            "fixture_id": fixture.id,
            "market": normalize_market(market),
            "outcome": normalize_market_pick(market, raw_outcome),
            "odds": odds,
            "odds_snapshot": json.dumps(odds_data),
            "timestamp": datetime.utcnow().isoformat()
        }]
        
        # Save with IMMEDIATE COMMIT (isolated transaction)
        self.save_predictions(predictions, run_id)
        
        return predictions
```

### Prediction Record Schema

```python
class PredictionRecord:
    id: int
    prediction_id: str          # UUID - unique identity
    fixture_id: int             # Foreign key to fixture
    market: str                # h2h, btts, ou25, ou15
    predicted_outcome: str     # Normalized pick (1/X/2, Yes/No, Over/Under)
    raw_outcome: str           # Original before normalization
    our_prob: float            # Raw model probability
    calibrated_prob: float     # After isotonic regression
    odds_decimal: float        # Bookmaker odds
    odds_snapshot: str         # JSON: {"odd_home": 2.0, "bookmaker": "bet365"}
    ev: float                 # Expected value
    is_legacy: bool            # 0 = new system, 1 = legacy
    run_id: str                 # Links to lineage
    timestamp: datetime         # When prediction was made
    settled: bool               # Has match been played?
    won: bool                   # Did bet win?
```

### Legacy Isolation

All queries filter by `is_legacy = 0`:

```python
# System truth example
total = s.execute(
    select(func.count(PredictionRecord.id))
    .where(PredictionRecord.is_legacy == 0)
).scalar()
```

This ensures:
- No legacy data contaminates governance
- UI shows only valid predictions
- Validation ignores old records

---

## E) Portfolio-First Capital Allocation

### Market Distribution Target

The system targets 25% allocation per market:
- h2h (home/draw/away): 25%
- btts (both score): 25%
- ou25 (over/under 2.5): 25%
- ou15 (over/under 1.5): 25%

### Allocation Process

```python
class PortfolioEngine:
    def allocate(self, candidates, bankroll):
        # 1. Compute expected returns from EV
        returns = np.array([c['ev'] for c in candidates])
        
        # 2. Build covariance matrix (from correlation engine)
        cov = self.correlation_engine.compute_covariance(candidates)
        
        # 3. Optimize with CVXPY (dual-mode)
        weights, status = self._markowitz_optimize(returns, cov)
        
        # 4. Apply Kelly criterion fractional sizing
        for candidate, weight in zip(candidates, weights):
            stake = weight * bankroll * 0.25  # Fractional Kelly
```

### CVXPY Optimization

Dual-mode Markowitz optimization:

1. **OSQP**: Primary solver (fast QP)
2. **SCS**: Fallback (splitting)
3. **ECOS**: Fallback (embedded)
4. **Heuristic**: Risk-adjusted proportional allocation

Constraints:
- `sum(weights) <= 1` (not equality - allows cash position)
- `0 <= weight <= max_weight`
- Regularization ensures positive semi-definite covariance

### Policy Constraints

PolicyEngine enforces rules:
- Max exposure per market: 30%
- Min odds threshold: 1.5
- Max single bet: 5% of bankroll
- Sweet spot flag for long-odds value (EV > threshold, odds > 3.5)

---

## F) Closed-Loop Validation (CLVE)

### Metrics

Before execution, CLVE evaluates:

| Metric | What It Measures | Threshold |
|--------|------------------|------------|
| **PDS** | Prediction divergence from baseline | < 0.3 |
| **AI** | Adaptation indicator | > 0.7 |
| **RR** | Return replication ability | > 0.5 |
| **PS** | Prediction stability | > 0.6 |
| **CDS** | Calibration drift | < 0.2 |

### Decision Logic

```python
class ClosedLoopValidationEngine:
    def evaluate(self, predictions, historical):
        pds = self._compute_pds(predictions)
        ai = self._compute_ai(historical)
        rr = self._compute_rr(historical)
        ps = self._compute_ps(predictions)
        cds = self._compute_cds(historical)
        
        scores = {"pds": pds, "ai": ai, "rr": rr, "ps": ps, "cds": cds}
        
        # Decision
        if ai < 0.5:
            return Decision(block=True, reason="NOT_ADAPTING")
        elif cds > 0.3:
            return Decision(block=True, reason="CALIBRATION_DRIFT")
        elif pds > 0.5:
            return Decision(block=True, reason="UNSTABLE_PREDICTIONS")
        else:
            return Decision(block=False, reason="PASSED")
```

### Adaptive Score

Composite score combining all metrics:

```python
adaptive_score = (
    0.25 * (1 - pds) +      # Lower is better
    0.30 * ai +              # Higher is better
    0.20 * rr +              # Higher is better
    0.15 * ps +              # Higher is better
    0.10 * (1 - cds)         # Lower is better
)
```

---

## G) Execution Runtime

### Process Isolation

The execution runtime runs as a separate process:

```bash
# Terminal 1: API
python backend/app.py

# Terminal 2: Execution Runtime  
python backend/runtime/execution_runtime.py
```

### Runtime Lock

Single-instance enforcement via file lock:

```python
class RuntimeLock:
    @staticmethod
    def acquire(instance_id):
        lock_file = Path("/tmp/bootball_runtime.lock")
        if lock_file.exists():
            raise RuntimeError("Runtime already running")
        lock_file.write_text(instance_id)
```

### Watchdog

ExecutionWatchdog monitors for:
- No heartbeat (stalled cycle)
- Silent failures (repeated crashes)
- No-prediction cycles
- No-bet cycles

---

## H) Observability & Monitoring

### System Truth API

Single endpoint provides all dashboard data:

```
GET /system/truth

{
  "predictions": {
    "total_count": 48,
    "legacy_count": 1664,
    "recent_count": 12,
    "markets": {"h2h": 12, "btts": 12, ...}
  },
  "lineage": {
    "total_runs_tracked": 10,
    "recent_runs": [
      {"run_id": "abc123", "status": "COMPLETE", "predictions": 48, ...}
    ]
  },
  "clve": {...},
  "temporal_governance": {...},
  "data_health": {...}
}
```

### Lineage Tracking

Each run creates JSON file in `data/lineage/`:

```json
{
  "run_id": "abc123",
  "system_version": "v2.1.0",
  "prediction_count": 48,
  "bet_count": 5,
  "status": "COMPLETE",
  "prediction_ids": ["uuid1", "uuid2", ...],
  "start_time": "2026-04-27T10:00:00",
  "end_time": "2026-04-27T10:05:00",
  "health_score": 0.85
}
```

### Health Dashboard

Access at `/tracking` shows:
- CLVE scores (PDS, AI, RR, PS, CDS)
- System health status
- Recent run status
- Validation warnings

---

## I) Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/football.db` | Main SQLite DB |
| `SCHEDULER_DB_PATH` | `./data/scheduler.db` | APScheduler DB |
| `API_FOOTBALL_KEY` | - | RapidAPI key |
| `RUNTIME_MODE` | `dev` | dev/live/backtest/live_eval |
| `BOT_ENABLED` | `false` | Enable betting |
| `BOT_MIN_EV` | `0.05` | Minimum EV (5%) |
| `BOT_MAX_STAKE` | `50` | Max stake in SEK |
| `EXECUTION_CYCLE_INTERVAL` | `1200` | Seconds (20 min) |
| `FORCE_PREDICTIONS` | `False` | Debug: force prediction generation |
| `EXPERIMENT_MODE` | `False` | Enable parameter variation |

### Database Schema

Key tables:
- `fixtures`: Match data
- `fixture_odds`: Bookmaker odds
- `prediction_records`: Generated predictions
- `placed_bets`: Executed bets
- `bankroll_rounds`: Round snapshots
- `model_versions`: Trained models
- `layer_governance_metrics`: Governance scores

---

## J) Troubleshooting

### No Predictions Visible

1. Check `FORCE_PREDICTIONS = True` in `scripts/run_continuous_cycle.py`
2. Verify fixtures with odds:
   ```sql
   SELECT COUNT(*) FROM fixture_odds;
   ```
3. Check lineage:
   ```bash
   ls -la data/lineage/
   ```

### CVXPY Falling Back to Heuristic

Check logs for solver status:
```
CVXPY optimization failed: Solver OSQP failed
Trying SCS...
```

Fix covariance matrix:
```python
# Ensure symmetric + PSD
cov = (cov + cov.T) / 2
cov += np.eye(n) * 1e-6
```

### Runs Showing as ACTIVE Forever

Verify lineage completion:
1. Check `data/lineage/` for JSON files
2. Check `/system/truth` lineage section
3. Verify `execution_runtime.py` logs show `RUN_END`

### JSON Parse Errors ("unexpected token '<'")

- Check Flask error handler is catching exceptions
- Verify endpoints return JSON, not HTML

### Database Lock Errors

- WAL mode enabled by default
- Use single session per operation
- Don't hold sessions across requests

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `python backend/app.py` | Start Flask API |
| `python backend/runtime/execution_runtime.py` | Start execution runtime |
| `sqlite3 data/football.db` | Query database |
| `python -c "from src.api.system_truth_snapshot import get_truth_response; print(get_truth_response().data)"` | Test system truth |

---

## Support

- Check `/system/debug/` endpoints for diagnostics
- Review logs in `/logs/`
- Run validation: `from src.governance.lineage_tracker import validate_prediction_consistency; validate_prediction_consistency()`