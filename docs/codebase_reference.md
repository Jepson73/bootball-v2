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
├── frontend/          Static assets and legacy React scaffold (inactive — UI is served by Flask via render_template_string)
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
| `python backend/runtime/execution_runtime.py` | Core execution process — runs `AgentCoordinator.run_cycle()` every 20 minutes |
| `python backend/app.py` | Alternative Flask entry point |
| `python scripts/migrate.py` | Run database schema migrations |
| `python scripts/backfill_all.py --seasons 2023 2022` | Backfill historical data |
| `python src/cli/backtest.py` | Historical strategy simulation |
| `python src/cli/event_replay.py` | Replay and debug event sequences |

### Startup Sequence

```
1. scripts/web_ui.py loaded (Flask + APScheduler)
2. config/settings.py initializes from .env (Pydantic)
3. RuntimeModeManager loads RUNTIME_MODE
4. Database initialized (src/storage/db.py)
5. APScheduler starts (backend/scheduler.py) — 6 auxiliary jobs only:
     fetch_fixtures (6h), fetch_results (1h), fetch_odds (1h),
     cleanup_matches (5m), live_settle (2m), daily_sanity_check (24h)
6. Flask routes exposed (/predictions, /betting, /admin, ...)
7. Event bus initialized (src/alerts/event_bus.py)

Core execution (predictions → bets) runs separately:
  backend/runtime/execution_runtime.py → AgentCoordinator.run_cycle()
  (every 20 minutes, as a separate process)
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

APScheduler auxiliary job definitions and circuit breaker.

- **6 registered auxiliary jobs:** `job_fetch_fixtures` (6h), `job_fetch_results` (1h), `job_fetch_odds` (1h), `job_cleanup_matches` (5m), `job_live_settle` (2m), `job_daily_sanity_check` (24h)
- 4 additional `job_*()` functions are defined but **not registered** in APScheduler: `job_auto_heal_runs`, `job_retrain_models`, `job_run_betting_bot`, `job_run_continuous_cycle` — these are superseded by `ExecutionRuntime`
- `_circuit_ok()` / `_circuit_failure()` — fault-tolerant job execution
- `is_job_allowed_in_mode()` — mode-based job filtering

### `backend/runtime/execution_runtime.py`

Core execution loop — the single spine driving predictions and bet placement.

- Runs as a separate process from the Flask web UI
- Calls `AgentCoordinator.run_cycle()` every 1200 seconds (20 minutes)
- `RuntimeLock` enforces single-instance operation (prevents concurrent cycles)
- Heartbeat watchdog updates every 60s during sleep
- This is the entry point for all betting activity; APScheduler only handles data-fetch auxiliary jobs

### `backend/execution_engine.py`

Legacy job dispatcher (singleton) — largely superseded by `ExecutionRuntime`.

- `JobType`, `ExecutionStatus` — enums
- `ExecutionEngine.execute()` — routes scheduler jobs to handlers
- Note: still imported by some scripts; the live betting path bypasses this entirely in favour of `AgentCoordinator.run_cycle()` via `ExecutionRuntime`

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
Predictor → Risk Manager → Execution Strategist → Portfolio Engine
  → Adversary (stress test) → Policy Engine → Save Bets
  → Feedback Loop → CLVE validation
```

- `AgentCoordinator.run_cycle()` — primary entrypoint called by `ExecutionRuntime` every 20 minutes
- `AgentCoordinator.run()` — thin wrapper that delegates to `run_cycle()`
- `_write_attribution()` — writes causal attribution for each decision

### `src/prediction/unified_prediction_service.py`

Single source of truth for all predictions.

- `UnifiedPredictionService.generate(fixtures=None)` — generic prediction entry point
- `UnifiedPredictionService.generate_with_fixture_data(fixture_objects)` — primary method called by coordinator; takes pre-loaded fixture ORM objects
- Applies `LeagueCalibrationEngine.apply()` to calibrate raw model probabilities before returning
- Standardizes prediction format; emits `PREDICTION_CREATED` events

### `src/betting/portfolio/portfolio_engine.py`

**Primary capital allocation orchestrator** — called directly by `AgentCoordinator`.

- Delegates QP solving to `markowitz_optimizer.py` (primary, SCS solver) with `cvxpy_optimizer.py` as fallback
- `_apply_learning_weights()` — applies market performance weights from `AdaptiveAllocator`
- `_enforce_market_caps()` — enforces per-market concentration limit (default 60%)

### `src/betting/portfolio_optimizer.py`

Legacy allocation module — **not called by coordinator**; semi-active.

- `PortfolioConfig` — diversification limits, correlation caps, max concentration
- `CandidateBet` — raw bet candidate with EV and Kelly fraction
- `OptimizedBet` — final bet with allocated stake
- Implements Markowitz mean-variance optimization and correlation-aware filtering independently of the primary portfolio engine path above

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

Probability calibration (per-model, single-tier).

- `Calibrator` / `PlattCalibrator` — Platt scaling (logistic regression on raw outputs)
- `get_calibration_cache(market)` — caches calibration parameters

### `src/calibration/league_calibration_engine.py`

Three-tier, per-league Platt-scaling calibration system.

- `LeagueCalibrationEngine.fit_all()` — fits calibration for every `(market, league_id)` pair with ≥25 settled samples; also fits L0000 global calibration
- `LeagueCalibrationEngine.apply(market, league_id, p_raw)` — resolution order:
  1. League-specific calibration (if ≥100 samples)
  2. Global (L0000) calibration
  3. Raw probability fallback
- Version label format: `v{model:02d}_c{cal:02d}_l{league:04d}_w{iteration:02d}`
  - L0000 = global calibration (league_id=NULL in DB)
  - League-specific baseline is compared against L0000 output (not raw) — positive improvement means the league cal genuinely beats global

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
  ↓  job_fetch_fixtures / job_fetch_odds  (APScheduler auxiliary)
fixtures, fixture_odds tables
  ↓  ExecutionRuntime → AgentCoordinator.run_cycle()  (every 20 min)
UnifiedPredictionService.generate_with_fixture_data()  →  prediction_records table
  ↓  PortfolioEngine.optimize()  (Markowitz via markowitz_optimizer.py)
CandidateBet → OptimizedBet
  ↓  PolicyEngine.validate()
Approved bets                                          →  placed_bets table
  ↓  job_fetch_results / job_live_settle  (APScheduler auxiliary)
fixtures.goals_home / goals_away updated
  ↓  settlement.settle_all()
placed_bets.settled_at, .result, .pnl
  ↓  LeagueCalibrationEngine.fit_all()  (calibration feedback loop)
league_calibrations table updated; drift may trigger model retrain
```

### Model Lifecycle

```
AgentCoordinator (when drift detected or training mode)
  ↓  src/models/trainer.py → Trainer.train_market()
GradientBoostingClassifier.fit(X, y)
  ↓  safe_model_save()
data/models/model_{market}_v{N}.pkl  (HMAC signed + .sig sidecar)
  ↓  ModelRegistry.register_retrain()
model_versions table  (is_active = False, version_label = "v{N:02d}_c00")
  ↓  ModelRegistry.activate()
data/model_{market}.pkl  ← active copy
  ↓  LeagueCalibrationEngine.fit_all()  (Platt-scaling per league)
league_calibrations table  (version_label = "v{N:02d}_c{C:02d}_l{league:04d}_w{W:02d}")
  ↓  UnifiedPredictionService.generate_with_fixture_data()
LeagueCalibrationEngine.apply() applies: league-specific → L0000 global → raw fallback
```

**Model version label format:** `v{model:02d}_c{cal:02d}_l{league:04d}_w{iteration:02d}`
- `v`: base model number (increments on retrain)
- `c`: global calibration number (increments on recalibration)
- `l`: league id (L0000 = global calibration)
- `w`: iteration number within this (model, cal, league) combination

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
| Singleton | `RuntimeModeManager`, `ExperimentTracker`, `ExecutionEngine`, `ModelRegistry`, `event_bus`, `LeagueCalibrationEngine` |
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

`allow_execution()` returns True for DEV, LIVE, and TRAINING. `allow_mutations()` (model training) returns True for DEV and TRAINING only.

| Mode | `allow_execution()` | `allow_mutations()` | Use Case |
|------|---------------------|---------------------|---------|
| `DEV` | Yes (real bets) | Yes | Default — full pipeline including live betting |
| `TRAINING` | Yes | Yes | Force retrain without restricting execution |
| `LIVE` | Yes (real bets) | No | Production — models frozen |
| `LIVE_EVAL` | No | No | Shadow / evaluation mode — predictions only |
| `BACKTEST` | No | No | Offline strategy evaluation on historical data |

---

## Scripts Quick Reference

| Script | Purpose | Status |
|--------|---------|--------|
| `scripts/web_ui.py` | **Main entry point** — Flask + APScheduler (auxiliary jobs only) | Active |
| `scripts/run_continuous_cycle.py` | Core execution pipeline — called by `ExecutionRuntime` | Active |
| `scripts/daily_run.py` | Data pipeline only (no prediction/betting) | Active |
| `scripts/backfill_all.py` | Historical data ingestion (multi-season) | Active |
| `scripts/backfill_cron.py` | Nightly incremental backfill (4am cron) | Active |
| `scripts/backfill_odds.py` | Odds-specific backfill | Active |
| `scripts/backfill_standings.py` | Standings-specific backfill | Active |
| `scripts/make_predictions.py` | Manual prediction generation | Active |
| `scripts/odds_poll.py` | Poll odds; recalculate EV | Active |
| `scripts/migrate.py` | Database schema migration runner | Active |
| `scripts/check_model.py` | Inspect trained model metadata | Diagnostic tool |
| `scripts/diagnostics.py` | Connectivity checks, backfill config validation | Diagnostic tool |
| `scripts/daily_sanity_check.py` | Sanity checks run by scheduler | Active |
| `scripts/auto_bet.py` | Legacy betting pipeline — **DEPRECATED** (not in live path) | Dead — kept for reference |
| `scripts/live_monitor.py` | Watch live matches in real-time | Likely dead |
