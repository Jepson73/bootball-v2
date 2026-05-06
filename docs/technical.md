# Bootball Technical Documentation

## A Comprehensive Guide for Development Teams

---

## Table of Contents

1. Introduction & Philosophy
2. System Architecture
3. Data Models & Schema
4. Core Services
5. ML Pipeline
6. Event System
7. Execution Runtime
8. Observability & API
9. Configuration
10. Deployment
11. Troubleshooting
12. Contributing

---

## 1. Introduction & Philosophy

Bootball is a **football prediction platform** combining machine learning with event-driven architecture. It generates value bets, allocates capital across markets, tracks performance, detects model drift, and improves through closed-loop validation.

### Core Principles

1. **Portfolio-First**: Capital allocation across all markets, not per-bet decisions
2. **Calibration Over Accuracy**: Models optimized for probability calibration, not classification accuracy
3. **Immutable Predictions**: Once generated, predictions are never recomputed
4. **Closed-Loop Validation**: System must prove it's learning before executing bets
5. **Observability**: Single source of truth for all dashboard data

### Why These Principles?

Research (Walsh & Joshi, 2024) showed:
- Models optimized for calibration: +34.69% ROI
- Models optimized for accuracy: -35.17% ROI

This means predicting correct outcomes matters less than predicting correct probabilities.

---

## 2. System Architecture

### Process Model

```
┌─────────────────────────────────────────────────────────────┐
│                    PROCESS ISOLATION                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────────┐     ┌──────────────────────────┐ │
│  │   Flask API Server   │     │  ExecutionRuntime       │ │
│  │   (gunicorn -w 1)    │     │  (standalone process)    │ │
│  │                      │     │                          │ │
│  │  - /system/truth    │     │  - run_cycle()          │ │
│  │  - /api/unified/*   │     │  - predictions          │ │
│  │  - WebSocket        │     │  - portfolio            │ │
│  │  - Health dashboard │     │  - execution            │ │
│  │                      │     │  - lineage              │ │
│  └──────────────────────┘     └──────────────────────────┘ │
│              │                              │                │
│              │   /system/truth            │                │
│              └───────────►◄───────────────┘                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
api-football (RapidAPI)
         │
         ▼
┌─────────────────┐
│  Ingestion     │
│  - fixtures    │
│  - odds        │
│  - lineups     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  UnifiedPredictionService │
│                            │
│  - prediction_id (UUID)  │
│  - odds_snapshot (JSON)   │
│  - market normalization   │
└────────┬─────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Prediction Records     │
│  (is_legacy=0)          │
└────────┬─────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐  ┌─────────────┐
│CLVE    │  │Portfolio    │
│Validation│  │Engine      │
│         │  │             │
│ PDS    │  │ Markowitz   │
│ AI     │  │ Kelly       │
│ RR     │  │ Correlation │
└────────┘  └──────┬──────┘
         │          │
         │          ▼
         │   ┌─────────────────┐
         │   │ ExecutionEngine │
         │   │                 │
         └──►│ - bet placement │
             │ - settlement    │
             └────────┬────────┘
                      │
                      ▼
             ┌─────────────────────┐
             │ LineageTracker      │
             │ (JSON persistence)   │
             └────────┬────────────┘
                      │
                      ▼
             ┌─────────────────────┐
             │ SystemTruth API    │
             │ /system/truth      │
             └─────────────────────┘
```

### Key Files

| Path | Purpose |
|------|---------|
| `backend/runtime/execution_runtime.py` | Independent execution process |
| `src/agents/coordinator.py` | Multi-agent pipeline orchestration |
| `src/prediction/unified_prediction_service.py` | Single source for predictions |
| `src/betting/portfolio/portfolio_engine.py` | Capital allocation |
| `src/governance/closed_loop_validation_engine.py` | CLVE metrics |
| `src/governance/lineage_tracker.py` | Run tracking |
| `src/api/system_truth_snapshot.py` | Unified observability |

---

## 3. Data Models & Schema

### Core Tables

```sql
-- Fixtures (matches)
CREATE TABLE fixtures (
    id INTEGER PRIMARY KEY,
    league_id INTEGER,
    season INTEGER,
    home_team_id INTEGER,
    away_team_id INTEGER,
    date DATETIME,
    status VARCHAR(10),  -- NS, FT, HT, ET
    goals_home INTEGER,
    goals_away INTEGER,
    ht_goals_home INTEGER,
    ht_goals_away INTEGER,
    outcome VARCHAR(1)  -- H, D, A
);

-- Odds snapshot
CREATE TABLE fixture_odds (
    id INTEGER PRIMARY KEY,
    fixture_id INTEGER,
    bookmaker VARCHAR(50),
    odd_home REAL,
    odd_draw REAL,
    odd_away REAL,
    odd_over REAL,
    odd_under REAL,
    odd_btts_yes REAL,
    odd_btts_no REAL,
    odd_over15 REAL,
    odd_under15 REAL
);

-- Predictions (immutable)
CREATE TABLE prediction_records (
    id INTEGER PRIMARY KEY,
    prediction_id VARCHAR(36) UNIQUE,  -- UUID
    fixture_id INTEGER,
    market VARCHAR(20),  -- h2h, btts, ou25, ou15
    predicted_outcome VARCHAR(10),  -- 1/X/2, Yes/No, Over/Under
    raw_outcome VARCHAR(10),
    our_prob REAL,
    calibrated_prob REAL,
    odds_decimal REAL,
    odds_snapshot VARCHAR(200),  -- JSON
    ev REAL,
    is_legacy BOOLEAN DEFAULT 0,
    run_id VARCHAR(36),
    timestamp DATETIME,
    settled BOOLEAN DEFAULT 0,
    won BOOLEAN
);

-- Bets (fake money)
CREATE TABLE placed_bets (
    id INTEGER PRIMARY KEY,
    round_id INTEGER,
    fixture_id INTEGER,
    market VARCHAR(20),
    outcome VARCHAR(10),
    stake REAL,
    odds REAL,
    our_prob REAL,
    ev REAL,
    settled BOOLEAN DEFAULT 0,
    won BOOLEAN,
    pnl REAL
);

-- Model versions
CREATE TABLE model_versions (
    id INTEGER PRIMARY KEY,
    market VARCHAR(20),
    version_number INTEGER,
    brier_score REAL,
    accuracy REAL,
    ece REAL,  -- Expected calibration error
    is_active BOOLEAN,
    model_type VARCHAR(20)  -- xgboost, lightgbm, logistic
);
```

### Prediction Identity

Every prediction gets a UUID and is immutable:

```python
prediction = {
    "prediction_id": "550e8400-e29b-41d4-a716-446655440000",
    "fixture_id": 12345,
    "market": "h2h",
    "outcome": "1",  # Normalized from "home"
    "raw_outcome": "home",
    "odds": 2.1,
    "odds_snapshot": '{"odd_home": 2.1, "bookmaker": "bet365"}',
    "our_prob": 0.45,
    "calibrated_prob": 0.48,
    "ev": 0.008,
    "is_legacy": False,
    "timestamp": "2026-04-27T10:00:00"
}
```

---

## 4. Core Services

### UnifiedPredictionService

Single source of truth for predictions:

```python
class UnifiedPredictionService:
    def generate_with_fixture_data(self, fixtures) -> list[dict]:
        """Generate predictions with prediction_id for each fixture."""
        for fixture in fixtures:
            for market in ["h2h", "btts", "ou25", "ou15"]:
                # Get model probabilities
                probs = get_model_prediction(market, home_id, away_id)
                
                # Normalize pick
                normalized = normalize_market_pick(market, best_outcome)
                
                # Get odds snapshot
                odds, snapshot = self._get_odds_for_market(fixture_id, market)
                
                yield {
                    "prediction_id": str(uuid.uuid4()),
                    "fixture_id": fixture.id,
                    "market": normalize_market(market),
                    "outcome": normalized,
                    "odds": odds,
                    "odds_snapshot": snapshot,
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def save_predictions(self, predictions, run_id) -> list[int]:
        """Save with ISOLATED transactions - each prediction commits immediately."""
        for pred in predictions:
            with get_session() as s:
                record = PredictionRecord(**pred)
                record.run_id = run_id
                record.is_legacy = False
                s.add(record)
                s.commit()  # Immediate commit - no rollback
```

### PortfolioEngine

Capital allocation across markets:

```python
class PortfolioEngine:
    def allocate(self, candidates, bankroll):
        # 1. Prepare returns (EV per bet)
        returns = np.array([c['ev'] for c in candidates])
        
        # 2. Build covariance matrix
        cov = self.correlation_engine.compute_covariance(candidates)
        
        # 3. Optimize with CVXPY (dual-mode)
        weights, status = self._markowitz_optimize(returns, cov)
        
        # 4. Apply fractional Kelly
        allocations = []
        for candidate, weight in zip(candidates, weights):
            stake = weight * bankroll * 0.25  # 25% Kelly
            allocations.append({
                **candidate,
                "stake": stake,
                "kelly_fraction": 0.25
            })
        
        return allocations
```

### CLVE (Closed-Loop Validation Engine)

Validates system adaptation before execution:

```python
class ClosedLoopValidationEngine:
    def evaluate(self, predictions, historical) -> Decision:
        pds = self._compute_pds(predictions)  # Prediction divergence
        ai = self._compute_ai(historical)      # Adaptation indicator
        rr = self._compute_rr(historical)      # Return replication
        ps = self._compute_ps(predictions)    # Prediction stability
        cds = self._compute_cds(historical)   # Calibration drift
        
        scores = {"pds": pds, "ai": ai, "rr": rr, "ps": ps, "cds": cds}
        
        # Block if not adapting
        if ai < 0.5:
            return Decision(block=True, reason="NOT_ADAPTING")
        if cds > 0.3:
            return Decision(block=True, reason="CALIBRATION_DRIFT")
        
        return Decision(block=False, scores=scores)
```

---

## 5. ML Pipeline

### Model Architecture

| Model | Market | Algorithm | Calibration |
|-------|--------|-----------|-------------|
| XGBoost | h2h | Gradient boosting | Isotonic regression |
| LightGBM | btts, ou25, ou15 | Gradient boosting | Isotonic regression |
| LogisticRegression | All | Logistic | Isotonic |

### Feature Engineering

Key features (priority order):

1. **ELO ratings** - Team strength from historical results
2. **Form (last 5)** - Recent performance
3. **Home/away stats** - Split by venue
4. **Head-to-head** - Historical matchups
5. **Proxy xG** - shots × 0.12 + shots_on_target × 0.25
6. **Odds movement** - Line movement signal
7. **Injuries** - Key player availability
8. **Lineup** - Confirmed lineups
9. **Goalkeeper** - GK absence as predictor

### Calibration

All raw probabilities calibrated via isotonic regression:

```python
from sklearn.isotonic import IsotonicRegression

# Train calibrator
calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds='clip')
calibrator.fit(probabilities, outcomes)

# Calibrate new predictions
calibrated = calibrator.transform(raw_probabilities)
```

---

## 6. Event System

### Event Bus

Central event dispatcher:

```python
class EventBus:
    def emit(self, event_type, payload):
        # 1. Log to in-memory buffer
        self._events.append({
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # 2. Dispatch to consumers
        for consumer in self._subscribers[event_type]:
            consumer.handle(payload)
        
        # 3. Persist to EventStore
        self._event_store.write(event_type, payload)
```

### Event Types

| Event | Description | Payload |
|-------|-------------|---------|
| PREDICTIONS_GENERATED | Predictions ready | count, timestamp |
| BET_PLACED | Bet submitted | bet_id, fixture, market, stake |
| BET_SETTLED | Match completed | bet_id, won, pnl |
| RUN_COMPLETED | Cycle finished | run_id, predictions, bets |
| CLVE_EVALUATED | Validation done | decision, scores |
| DRIFT_DETECTED | Model drift | market, drift_score |

### Consumers

Event consumers handle side effects:

- **DiscordConsumer**: Send alerts
- **BettingDashboardConsumer**: Update web state
- **HealthDashboardConsumer**: Update health metrics
- **ModelTrendConsumer**: Track model performance
- **CalibrationConsumer**: Update calibration curves

---

## 7. Execution Runtime

### Runtime Process

Runs as standalone process, separate from Flask:

```python
# backend/runtime/execution_runtime.py
class ExecutionRuntime:
    def execute_cycle(self):
        run_id = str(uuid.uuid4())[:8]
        
        logger.info(f"RUN_START: run_id={run_id}")
        
        # Initialize lineage
        lineage_tracker.start_lineage(run_id)
        
        try:
            # Run coordinator
            result = coordinator.run_cycle()
            
            predictions = result.get("predictions", 0)
            
            # Hard assertion
            if predictions == 0:
                raise RuntimeError("PIPELINE DEAD: NO PREDICTIONS")
            
            # Complete lineage
            lineage_tracker.set_run_metrics(
                prediction_count=predictions,
                bet_count=result.get("bets", 0),
                health_score=1.0
            )
            lineage_tracker.complete_lineage("COMPLETE")
            
            logger.info(f"RUN_END: run_id={run_id}, status=success")
            
        except Exception as e:
            lineage_tracker.complete_lineage("FAILED")
            logger.error(f"RUN_END: run_id={run_id}, status=failed")
            raise
```

### Watchdog

Monitors runtime health:

```python
class ExecutionWatchdog:
    def check_health(self):
        # Check heartbeat
        if not self._last_heartbeat:
            return "NO_HEARTBEAT"
        
        # Check consecutive failures
        if self._consecutive_failures > 3:
            return "REPEATED_FAILURES"
        
        # Check empty predictions
        if self._consecutive_empty_predictions > 5:
            return "NO_PREDICTIONS"
        
        return "HEALTHY"
```

---

## 8. Observability & API

### System Truth API

Single endpoint for all dashboard data:

```python
@app.route('/system/truth')
def system_truth():
    return jsonify(get_truth_response().to_dict())
```

Returns:

```python
{
    "system_status": {"mode": "DEV", "status": "OPERATIONAL"},
    "execution": {...},
    "pipeline": {...},
    "predictions": {
        "total_count": 48,
        "legacy_count": 1664,
        "recent_count": 12,
        "markets": {"h2h": 12, "btts": 12, "ou25": 12, "ou15": 12},
        "sample": [...]
    },
    "lineage": {
        "total_runs_tracked": 10,
        "recent_runs": [
            {"run_id": "abc123", "status": "COMPLETE", "predictions": 48, "bets": 5}
        ]
    },
    "clve": {...},
    "temporal_governance": {...},
    "data_health": {...}
}
```

### Lineage Tracking

Persists run data to JSON:

```bash
# data/lineage/
lineage_abc123_2026-04-27T10-00-00.json
lineage_def456_2026-04-27T10-20-00.json
```

```json
{
    "run_id": "abc123",
    "system_version": "v2.1.0",
    "prediction_count": 48,
    "bet_count": 5,
    "prediction_ids": ["uuid1", "uuid2", ...],
    "portfolio_id": "p_abc123",
    "status": "COMPLETE",
    "start_time": "2026-04-27T10:00:00",
    "end_time": "2026-04-27T10:05:00",
    "health_score": 0.85,
    "experiment": false,
    "strategy_variant": "baseline"
}
```

---

## 9. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/football.db` | SQLite path |
| `API_FOOTBALL_KEY` | - | RapidAPI key |
| `RUNTIME_MODE` | `dev` | dev/live/backtest/live_eval |
| `EXECUTION_CYCLE_INTERVAL` | `1200` | Seconds between runs |
| `BOT_ENABLED` | `false` | Enable betting |
| `BOT_MIN_EV` | `0.05` | Minimum EV threshold |
| `BOT_MAX_STAKE` | `50` | Max stake in SEK |
| `FORCE_PREDICTIONS` | `False` | Debug: force predictions |

### Runtime Modes

| Mode | Description |
|------|-------------|
| `dev` | Full flexibility, no restrictions |
| `live` | Frozen system, stricter validation |
| `backtest` | Historical simulation |
| `live_eval` | Live evaluation without betting |

---

## 10. Deployment

### Development

```bash
# Terminal 1: API
python backend/app.py

# Terminal 2: Execution Runtime
python backend/runtime/execution_runtime.py

# Access UI
# http://localhost:5000
# Password: bootball2026
```

### Production

```bash
# Start Execution Runtime first (locks singleton)
python backend/runtime/execution_runtime.py &

# Start API with gunicorn
gunicorn -w 1 --threads 2 -b 0.0.0.0:5000 backend.app:app
```

### Database

```bash
# Initialize
python scripts/setup_db.py

# Check
sqlite3 data/football.db ".schema"
```

---

## 11. Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| No predictions | Set `FORCE_PREDICTIONS = True` |
| CVXPY fallback | Check covariance matrix |
| Run stuck in ACTIVE | Check lineage completion |
| JSON errors | Verify error handlers |

### Validation

```python
# Run prediction validation
from src.governance.lineage_tracker import validate_prediction_consistency
validate_prediction_consistency()
```

---

## 12. Contributing

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings to public methods

### Testing

```bash
# Run tests
pytest tests/

# Coverage
pytest --cov=src tests/
```

### Adding Features

1. Create feature branch
2. Implement changes
3. Update tests
4. Update documentation
5. Submit PR

---

## Appendix: Key Metrics

### CLVE Metrics

| Metric | Full Name | Good | Bad |
|--------|-----------|------|-----|
| PDS | Prediction Divergence Score | < 0.3 | > 0.5 |
| AI | Adaptation Indicator | > 0.7 | < 0.5 |
| RR | Return Replication | > 0.5 | < 0.3 |
| PS | Prediction Stability | > 0.6 | < 0.4 |
| CDS | Calibration Drift Score | < 0.2 | > 0.3 |

### Market Encoding

| Market | Picks |
|--------|-------|
| h2h | 1 (home), X (draw), 2 (away) |
| btts | Yes, No |
| ou25 | Over, Under |
| ou15 | Over, Under |