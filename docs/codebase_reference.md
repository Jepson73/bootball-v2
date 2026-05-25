# Bootball Codebase Reference

Autonomous football betting intelligence platform — Flask + multi-agent + portfolio-first architecture.

---

## Directory Structure

```
/opt/projects/bootball/
├── backend/           Core platform infrastructure (scheduler, runtime mode, execution engine)
├── src/               Main application logic (models, agents, betting, storage, events)
│   ├── agents/        Multi-agent coordinator
│   ├── analytics/     Reporting and analysis
│   ├── api/           Internal API utilities
│   ├── alerts/        Event bus and notification routing
│   ├── backtesting/   Historical simulation framework
│   ├── betting/       Portfolio optimizer, Kelly, EV, risk decisions
│   ├── calibration/   State calibration engine
│   ├── cache/         Prediction caching
│   ├── cli/           Command-line tools (backtest, event replay)
│   ├── contracts/     Pipeline stage data contracts
│   ├── evaluation/    Model metrics (Brier score, Sharpe, calibration)
│   ├── events/        Event-driven architecture (types, store, routing)
│   ├── features/      Feature engineering (Elo, form, strength, xG)
│   ├── governance/    Policy engine, closed-loop validation, lineage tracking
│   ├── handlers/      Event handlers (settlement, odds, backfill)
│   ├── ingestion/     API-Football v3 client, backfill pipeline
│   ├── learning/      Feedback loop, weight optimizer, event replay
│   ├── maintenance.py Database cleanup utilities
│   ├── models/        ML model training, calibration, registry, drift detection
│   ├── monitoring/    Health checks, drift coordinator
│   ├── notifications/ Discord notifier, agent reporter
│   ├── performance/   Performance metrics tracker
│   ├── portfolio/     Self-optimizing and adaptive allocators
│   ├── prediction/    Unified prediction service, market normalizer
│   ├── realtime/      WebSocket server, event streaming
│   ├── security/      HMAC model signing, validation, rate limiting
│   ├── settlement.py  Bet settlement, result fetching, P/L calculation
│   ├── simulation/    Monte Carlo engine
│   ├── state/         Betting state, snapshot store, state reconstructor
│   └── storage/       SQLAlchemy ORM models and DB session factory
├── config/            Settings, leagues, markets, drift thresholds
├── scripts/           Executable scripts and CLI utilities
├── frontend/          React/Vite web UI
├── tests/             Pytest test suites
├── migrations/        Database migration scripts
├── data/              Runtime data: DB, trained models, logs
├── docs/              Documentation
├── logs/              Application logs
└── reports/           Closed-loop validation and calibration reports
```

---

## Entry Points

| Command | Purpose |
|---------|---------|
| `python scripts/web_ui.py` | **Primary** — Flask UI + embedded APScheduler on port 5000 |
| `python backend/app.py` | Alternative Flask entry point |
| `python scripts/setup_db.py` | Initialize empty database |
| `python scripts/migrate.py` | Run database schema migrations |
| `python scripts/backfill_all.py --seasons 2023 2022` | Backfill historical data |
| `python src/cli/backtest.py` | Historical strategy simulation |
| `python src/cli/event_replay.py` | Replay and debug event sequences |

### Startup Sequence

```
1. app.py / web_ui.py loaded
2. config/settings.py initializes from .env (Pydantic)
3. RuntimeModeManager loads RUNTIME_MODE
4. Database initialized (src/storage/db.py)
5. APScheduler starts (backend/scheduler.py)
6. Jobs scheduled: fetch_fixtures (6h), fetch_results (1h),
   fetch_odds (2h), run_predictions (daily), retrain_models (weekly),
   run_betting_bot (continuous via AgentCoordinator)
7. Flask routes exposed (/predictions, /betting, /admin, ...)
8. Event bus initialized (src/alerts/event_bus.py)
```

---

## Configuration

```
.env (secrets, runtime flags)
  └── config/settings.py     Pydantic Settings singleton — single source of truth
        └── backend/config.py  Backward-compatible Config class
```

| File | Purpose |
|------|---------|
| `config/settings.py` | All env-based settings: API keys, scheduling, model dirs, runtime mode |
| `config/leagues.py` | `ALL_LEAGUE_IDS` — 1,225 leagues; league metadata; season definitions |
| `config/markets.py` | Market definitions (h2h, btts, ou25, ou15); outcome mappings |
| `config/drift_thresholds.py` | Drift detection thresholds; retrain triggers |
| `.env.example` | Template for required environment variables |

---

## Backend Module Reference

### `backend/runtime_mode.py`

Unified runtime mode enforcement.

- `RuntimeMode` — enum: `DEV`, `LIVE`, `LIVE_EVAL`, `TRAINING`, `BACKTEST`
- `RuntimeModeManager` — singleton; loaded from `RUNTIME_MODE` env var
- `@mode_guard([...modes])` — decorator that raises `RuntimeError` if current mode not in list
- `require_training_or_dev()`, `block_in_live_eval()` — convenience guards

### `backend/scheduler.py`

APScheduler job definitions and circuit breaker.

- 11 `job_*()` functions (fetch_fixtures, fetch_odds, run_predictions, retrain_models, etc.)
- `_circuit_ok()` / `_circuit_failure()` — fault-tolerant job execution
- `is_job_allowed_in_mode()` — mode-based job filtering

### `backend/execution_engine.py`

Central execution dispatcher (singleton).

- `JobType`, `ExecutionStatus` — enums
- `ExecutionEngine.execute()` — only valid entry point for job execution; validates pipeline contracts

### `backend/experiment_tracker.py`

Tracks experiment runs end-to-end (singleton).

- `SystemSnapshot` — captures model versions, config hash, run metadata
- `ExperimentTracker.start_run()`, `capture_system_snapshot()`, `finalize_run()`
- Writes to `experiment_runs` table; run artifacts viewable at `/runs/{run_id}`

### `backend/auto_healing_engine.py`

Detects and repairs broken experiment runs.

- `RunHealthAnalyzer` — diagnoses missing pipeline stages
- `AutoHealingEngine` — replays missing stages to restore run integrity

### `backend/causal_graph.py`

Graph-based decision explainability.

- `DecisionNode`, `DecisionEdge`, `CausalGraph`
- Tracks decision influences for audit trail

---

## Source Module Reference

### `src/storage/db.py`

SQLAlchemy database layer.

- `get_engine()`, `get_session_maker()`, `init_db()`
- `get_session()` — context manager returning a scoped session

### `src/storage/models.py`

40+ SQLAlchemy ORM models covering the full football domain.

Key models:

| Model | Key Fields |
|-------|-----------|
| `Fixture` | id, league_id, home_team_id, away_team_id, date, status, goals_home, goals_away |
| `Team` | id, name, country, logo_url |
| `League` | id, name, country, season |
| `FixtureOdds` | fixture_id, market, bookmaker, outcome, decimal_odds, updated_at |
| `PredictionRecord` | fixture_id, market, our_prob, implied_prob, ev, kelly, run_id |
| `PlacedBet` | fixture_id, market, outcome, stake, odds, placed_at, settled_at, result, pnl |
| `ModelVersion` | market, version_label, is_active, brier_score, log_loss, trained_at |
| `Bankroll` | balance, currency, updated_at |
| `Calibration` | market, method, params_json, calibrated_at |
| `EloRating` | team_id, league_id, rating, updated_at |

### `src/agents/coordinator.py`

**Central execution spine.** Orchestrates the full prediction-to-execution pipeline.

Pipeline stages:
```
Predictor agent → Risk manager → Execution strategist → Portfolio engine → Policy engine → Execution
```

- `AgentCoordinator.run(fixtures, run_context)` — main entrypoint
- `_write_attribution()` — writes causal attribution for each decision

### `src/prediction/unified_prediction_service.py`

Single source of truth for all predictions.

- `UnifiedPredictionService.generate(fixture, markets, run_context)`
- Standardizes prediction format; emits `PREDICTION_CREATED` events

### `src/betting/portfolio_optimizer.py`

Global capital allocation across markets.

- `PortfolioConfig` — diversification limits, correlation caps, max concentration
- `CandidateBet` — raw bet candidate with EV and Kelly fraction
- `OptimizedBet` — final bet with allocated stake
- Markowitz mean-variance optimization; correlation-aware filtering

### `src/betting/kelly.py`

Kelly criterion stake sizing.

- `kelly_fraction(p, b)` — full Kelly
- `fractional_kelly(p, b, fraction)` — risk-reduced Kelly
- `kelly_stake(bankroll, p, b, fraction, cap)` — final stake with bankroll cap

### `src/betting/ev.py`

Expected value and implied probability.

- `expected_value(prob, decimal_odds)` → float
- `implied_probability(decimal_odds)` → float

### `src/betting/shin.py`

Fair probability extraction from bookmaker odds.

- `shin_probabilities(odds_list)` — removes overround via Shin model
- `overround(odds_list)` — calculates bookmaker margin

### `src/models/model_registry.py`

Model version lifecycle management (singleton).

- `register_retrain(market, path, metrics)` — registers new trained model
- `register_recalibration(market, path)` — registers recalibrated model
- `activate(market, version_id)` — promotes version to active
- `compare(market, v1, v2)` — performance comparison
- `load_artifacts(market)` — loads active `.pkl` from `data/models/`

### `src/models/trainer.py`

Per-market model training.

- Markets: `h2h` (1X2), `btts` (BTTS yes/no), `ou25` (over/under 2.5), `ou15` (over/under 1.5)
- `GradientBoostingClassifier` with feature building
- `get_cache_path(market)` — deterministic cache key for training artifacts

### `src/models/calibrator.py`

Probability calibration.

- `Calibrator` / `PlattCalibrator` — Platt scaling (logistic regression on raw outputs)
- `get_calibration_cache(market)` — caches calibration parameters

### `src/governance/policy_engine.py`

Risk constraint enforcement.

- `PolicyEngine.validate(candidate_bets)` → `PolicyDecision`
- Enforces: max correlation, max market concentration, minimum margin threshold

### `src/settlement.py`

Bet settlement pipeline.

- `get_market_result(fixture, market)` — determines win/loss/push for each market
- `can_settle_early(fixture, market)` — checks if BTTS/Over 2.5 can be settled before FT
- `settle_all()` — main pipeline; calculates P/L; updates `PlacedBet` records
- `update_live_fixture_statuses()` — fetches live fixtures; syncs DB status
- `fetch_and_update_fixtures()` / `backfill_missing_scores()` — result corrections

### `src/ingestion/client.py`

API-Football v3 client.

- `APIFootballClient` — rate-limited, response-cached client
- `calls_used_today()` / `calls_remaining_today()` — API quota tracking
- Caches responses to avoid redundant API calls

### `src/security/safe_load.py`

HMAC-SHA256 signed model persistence.

- `safe_model_save(model, path)` — saves model + detached `.sig` file
- `safe_model_load(path)` — verifies signature before loading; raises on tamper

### `src/alerts/event_bus.py`

System-wide event pub/sub (singleton).

- `event_bus` — global event bus instance
- `Events` enum — all registered event types
- `event_bus.emit(Events.X, payload)` / `event_bus.on(Events.X, handler)`

---

## Key Data Flows

### Fixture → Prediction → Bet → Settlement

```
API-Football
  ↓  job_fetch_fixtures / job_fetch_odds
fixtures, fixture_odds tables
  ↓  run_continuous_cycle → AgentCoordinator.run()
UnifiedPredictionService.generate()       →  prediction_records table
  ↓  PortfolioOptimizer.optimize()
CandidateBet → OptimizedBet
  ↓  PolicyEngine.validate()
Approved bets                             →  placed_bets table
  ↓  job_fetch_results
fixtures.goals_home / goals_away updated
  ↓  settlement.settle_all()
placed_bets.settled_at, .result, .pnl
  ↓  calibration feedback loop
model retrain triggered if drift detected
```

### Model Lifecycle

```
retrain_models_new.py (scheduler)
  ↓  Trainer.train_market()
GradientBoostingClassifier.fit(X, y)
  ↓  safe_model_save()
data/models/model_{market}_v{N}.pkl  (HMAC signed)
  ↓  ModelRegistry.register_retrain()
model_versions table  (is_active = False)
  ↓  ModelRegistry.activate()
data/model_{market}.pkl  ← active symlink/copy
  ↓  Prediction pipeline
get_model_prediction()  uses data/model_{market}.pkl
```

### Runtime Mode Guard Flow

```
.env RUNTIME_MODE=live
  ↓
RuntimeModeManager._mode = RuntimeMode.LIVE
  ↓  Scheduler job check
is_job_allowed_in_mode("retrain_models", LIVE) → False → skip
  ↓  Decorator check
@mode_guard([TRAINING, DEV]) on train functions → raises RuntimeError in LIVE
```

---

## ML Markets

| Market | Task | Output Classes |
|--------|------|---------------|
| `h2h` | Match result | `1` (home), `X` (draw), `2` (away) |
| `btts` | Both teams to score | `yes`, `no` |
| `ou25` | Over/under 2.5 goals | `over`, `under` |
| `ou15` | Over/under 1.5 goals | `over`, `under` |

All markets use `GradientBoostingClassifier` with Platt calibration. Features include Elo ratings, recent form, head-to-head stats, expected goals, and league-normalized strength metrics.

---

## Architecture Patterns

| Pattern | Where Used |
|---------|-----------|
| Singleton | `RuntimeModeManager`, `ExperimentTracker`, `ExecutionEngine`, `ModelRegistry`, `event_bus` |
| Context Manager | `get_session()` for DB transactions |
| Decorator | `@mode_guard()`, `@require_training_or_dev()` for mode authorization |
| Factory | `create_app()`, `get_model_registry()`, `get_bankroll_manager()` |
| Pipeline | fetch → predict → portfolio → execute → settle |
| Event-Driven | Central `event_bus` pub/sub across modules |
| Strategy | Pluggable market models (h2h, btts, ou25, ou15) |
| Repository | DB access via SQLAlchemy ORM |

---

## Testing

```bash
pytest                            # All tests
pytest -m "not ml and not api"    # Fast subset (no API calls, no slow ML)
pytest --cov=src                  # With coverage report
```

| Marker | Meaning |
|--------|---------|
| `@pytest.mark.ml` | Slow; skipped unless explicitly included |
| `@pytest.mark.api` | Requires API key or sandbox |
| `@pytest.mark.bot` | Betting bot tests; never run with `BOT_ENABLED=true` |
| `@pytest.mark.db` | Requires SQLite connection |

Key test files:

| Path | Coverage |
|------|---------|
| `tests/test_betting.py` | EV, Kelly, Shin, calibration metrics |
| `tests/integration/test_portfolio_optimizer.py` | Capital allocation |
| `tests/integration/test_policy_constraints.py` | Risk constraint enforcement |
| `tests/integration/test_safe_load.py` | Model signing/loading security |
| `tests/models/test_calibration.py` | Platt calibration |
| `tests/models/test_drift.py` | Drift detection |
| `tests/security/test_validation.py` | Input validation |
| `tests/web_ui/test_predictions_api.py` | Prediction API endpoints |

---

## Runtime Modes Quick Reference

| Mode | Betting | Training | Live Data | Use Case |
|------|---------|----------|-----------|---------|
| `DEV` | Simulated | Allowed | Mock | Local development |
| `TRAINING` | Blocked | Allowed | Real | Model training runs |
| `LIVE` | Real stakes | Blocked | Real | Production |
| `LIVE_EVAL` | Simulated | Blocked | Real | Shadow mode / paper trading |
| `BACKTEST` | Simulated | Blocked | Historical | Strategy evaluation |

---

## Scripts Quick Reference

| Script | Purpose |
|--------|---------|
| `scripts/web_ui.py` | **Main entry point** — Flask + scheduler |
| `scripts/daily_run.py` | Data pipeline only (no prediction/betting) |
| `scripts/backfill_all.py` | Historical data ingestion |
| `scripts/backfill_cron.py` | Nightly incremental backfill (4am) |
| `scripts/make_predictions.py` | Generate predictions for upcoming fixtures |
| `scripts/odds_poll.py` | Poll odds; recalculate EV |
| `scripts/retrain_models_new.py` | Retrain all market models |
| `scripts/settle_bets.py` | Manual bet settlement |
| `scripts/migrate.py` | Database schema migration |
| `scripts/setup_db.py` | Initialize empty database |
| `scripts/check_model.py` | Inspect trained model metadata |
| `scripts/evaluate_model.py` | Evaluate model on holdout data |
| `scripts/live_monitor.py` | Watch live matches in real-time |
| `scripts/send_alerts.py` | Test Discord notifications |
