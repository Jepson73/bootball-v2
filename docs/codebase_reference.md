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
├── scripts/           Executable scripts and CLI utilities (scripts/__init__.py makes it a package for gunicorn)
├── v2/                Bootball V2 web UI package (auth_v2, db_v2, templates_v2, routes/)
├── frontend/          Static assets and legacy React scaffold (inactive — UI is served by Flask via render_template_string)
├── tests/             Pytest test suites
├── migrations/        Database migration scripts
├── data/              Runtime data: DB, trained models, logs
├── docs/              Documentation (codebase_reference.md, deployment_state.md)
├── logs/              Application logs
└── reports/           Closed-loop validation and calibration reports
```

---

## Entry Points

| Command | Purpose |
|---------|---------|
| `python scripts/web_ui_v2.py` | **Primary UI (V2)** — two-track Flask UI on port 5000; Track A accuracy + forward-collection + predictions; no V1 imports |
| `gunicorn -w 1 -b 0.0.0.0:5001 scripts.web_ui:app` | **V1 UI (reference)** — legacy Flask UI on port 5001 via `bootball-web.service` |
| `python backend/runtime/execution_runtime.py` | Core execution process — runs `AgentCoordinator.run_cycle()` every 20 minutes |
| `python backend/app.py` | Alternative Flask entry point |
| `python scripts/migrate.py` | Run database schema migrations |
| `python scripts/backfill_all.py --seasons 2023 2022` | Backfill historical data |
| `python src/cli/backtest.py` | Historical strategy simulation |
| `python src/cli/event_replay.py` | Replay and debug event sequences |

### Startup Sequence

Two web services run in parallel (both managed by systemd):

```
bootball-web-v2.service  →  scripts/web_ui_v2.py (port 5000, primary)
  1. Flask app created; blueprints registered (home, track_a, predictions, collection, explorer)
  2. init_db() via src/storage/db.py
  3. All routes protected by require_auth() from v2/auth_v2.py
     (cookie: authenticated_v2; no V1 cookie collision)

bootball-web.service  →  gunicorn scripts.web_ui:app (port 5001, V1 reference)
  1. scripts/web_ui.py loaded (Flask + embedded APScheduler)
  2. APScheduler starts (backend/scheduler.py) — 6 auxiliary jobs:
       fetch_fixtures (6h), fetch_results (1h), fetch_odds (1h),
       cleanup_matches (5m), live_settle (2m), daily_sanity_check (24h)

bootball-runtime.service (separate process):
  backend/runtime/execution_runtime.py → AgentCoordinator.run_cycle()
  (every 20 minutes; all predictions → portfolio → bets)
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
| `config/settings.py` | All env-based settings: API keys, scheduling, model dirs, runtime mode; `backfill_daily_cap` (default 60 000) soft-caps backfill quota; leagues 777/778/779/648 added to `calendar_year_leagues` |
| `config/leagues.py` | `ALL_LEAGUE_IDS` — 1,225 leagues; league metadata; season definitions |
| `config/forward_leagues.py` | Forward-collection leagues (Pinnacle-covered, high goal-rate); capture bookmakers (Pinnacle, Bet365); market types (h2h, o/u 2.5, BTTS); stale-window constant |
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
| `PredictionRecord` | fixture_id, market, our_prob, calibrated_prob, blended_prob, implied_prob, ev, run_id, prob_home, prob_draw, prob_away (h2h only), data_context (Phase 16b: `full`/`elo_both`/`elo_partial`/`flat_prior`/`national_elo`) |
| `PlacedBet` | fixture_id, market, outcome, stake, odds, placed_at, settled_at, result, pnl |
| `ModelVersion` | market, version_label, is_active, brier_score, log_loss, trained_at |
| `Bankroll` | balance, currency, updated_at |
| `Calibration` | market, method, params_json, calibrated_at |
| `EloRating` | team_id, rating, games_played, as_of_date, pool (`club`/`national`, Phase 16b) |
| `OddsSnapshot` | fixture_id, bookmaker_id, bookmaker_name, market_type, captured_at, odd_home, odd_draw, odd_away, odd_over, odd_under, odd_btts_yes, odd_btts_no |

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
- `save_predictions()` — writes h2h prob vector to `prob_home/prob_draw/prob_away` (keys "1"/"X"/"2") for use by `evaluate_track_a()`; also back-fills the vector in both skip paths ("both preliminary" and "downgrade") when `prob_home is None`, so existing NULL-vector records self-heal on the next prediction cycle
- `evaluate_track_a(market, settled_records)` — scores settled predictions: log-loss, Brier, AUC; h2h requires `prob_home` on each record

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
- `get_api_status()` — live quota from `/status` endpoint (cached 2 min)
- Cache files live at `data/raw/api_cache/api_cache/` (`CACHE_DIR`); cache reads/writes both target this path

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

**Phase 16b–19 — Elo hybrid predictions (club + national pools)**

`src/features/elo.py` — `EloEngine` with fixed draw model (exponential decay: `p_draw = 0.30 * exp(-|delta| / 400)`), pool-scoped `_get_current_rating()`, `predict(pool=)`, and `predict_from_ratings()`. `update_all_ratings(pool)` clears and rebuilds one pool without touching the other:
- `pool='club'`: filter `league.country != 'World'`; processes 750K+ domestic-league FT fixtures; **20,930 teams rated**.
- `pool='national'`: filter `league.id IN NATIONAL_POOL_LEAGUES` (18 competitions: WC, WC quals by confederation, UEFA/CONCACAF Nations Leagues, Euro, AFCON, Copa America, Asian Cup, senior Friendlies); processes ~7K FT fixtures; **583 national teams rated** (range 1037–1922).

`scripts/generate_gap_predictions.py` — one-shot: writes h2h `PredictionRecord`s for club NS fixtures missing a prediction. Hybrid logic: both rated → `elo_both`; Friendlies + one unrated → `flat_prior` (H43/D27/A30); non-Friendly + one unrated → `elo_partial` (1500 default); Youth keyword → abstain. `INSERT OR IGNORE` for idempotency.

`scripts/update_national_ratings.py` — rebuilds `pool='national'` Elo ratings from the `NATIONAL_POOL_LEAGUES` whitelist. Prints isolation report (bridge teams, club-pool row count unchanged). Run before `generate_wc_predictions.py`.

`scripts/generate_wc_predictions.py` — writes national Elo predictions for the 11 World Cup NS fixtures via `predict(pool='national')`, `data_context='national_elo'`. Uses UPDATE (not INSERT) since records already exist from the ensemble pipeline. Youth competitions (league_id 493/918) are abstained.

**Phase 18 — per-outcome soft-book odds display**

`v2/db_v2.py` — `_fetch_soft_odds(fixture_ids)` consolidates `fixture_odds` rows (one bookmaker per fixture; Bet365 preferred). `_attach_soft_odds()` merges the result into market dicts for both `get_predictions_for_upcoming()` and `get_explorer_data()`. Fields added to each market dict: `soft_book`, `soft_home/draw/away`, `soft_over/under`, `soft_over15/under15`, `soft_btts_yes/no`. Pinnacle is always excluded from soft odds.

`v2/routes/predictions_v2.py` — `_format_market()` renders per-outcome prices inline beside their probabilities: H2H shows `H 56% (1.78) D 24% (3.60) A 20% (4.20)` with a compact bookmaker label; O/U and BTTS show the predicted-side price. Soft prices never appear in Track B (EV column remains Pinnacle-only and unchanged).

`v2/routes/explorer_v2.py` — `_mkt_cell()` extended: H2H distribution line shows per-outcome prices; binary markets show the predicted-side price inline. Same bookmaker label treatment as predictions view.

**Phase 20 (Task 6) — limbo fixture visibility**

Investigation revealed 153 fixtures with unsettled predictions not visible in the predictions view (89 past-dated NS with stale dates + 49 permanently voided PST/CANC/AWD + 15 temporarily in-play). These ARE reachable in the explorer, but were previously unlabelled — a CANC fixture looked identical to a valid NS upcoming match.

`v2/db_v2.py` — `get_explorer_data()` now includes `Fixture.status` in the outer query and returns `fixture_status` in each fixture dict.

`v2/routes/explorer_v2.py` — `_status_badge(status)` renders an inline badge for non-NS/FT fixtures: green `LIVE`/`HT` for in-play statuses; gray `POSTPONED`/`CANCELLED`/`AWARDED`/`ABANDONED` for void statuses. NS and FT fixtures show no badge. Badge appears in the match name cell next to the team names. No prediction records are mutated; the fix is display-only.

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
| `scripts/web_ui_v2.py` | **Primary UI (V2)** — two-track Flask app on port 5000; strict V1 isolation; registers v2/ blueprints (home, track_a, predictions, collection, explorer) | Active |
| `scripts/web_ui.py` | V1 Flask UI + APScheduler on port 5001 (via gunicorn in `bootball-web.service`); reference build | Active |
| `scripts/run_continuous_cycle.py` | Core execution pipeline — called by `ExecutionRuntime` | Active |
| `scripts/daily_run.py` | Data pipeline only (no prediction/betting); enforces `backfill_daily_cap` in `_fetch_completed()`; logs per-run quota snapshots to `logs/quota_log.csv` | Active |
| `scripts/backfill_all.py` | Historical data ingestion (multi-season) | Active |
| `scripts/backfill_cron.py` | Nightly incremental backfill (4am cron) | Active |
| `scripts/backfill_odds.py` | Odds-specific backfill | Active |
| `scripts/backfill_standings.py` | Standings-specific backfill | Active |
| `scripts/make_predictions.py` | Manual prediction generation | Active |
| `scripts/odds_poll.py` | Poll odds; recalculate EV | Active |
| `scripts/migrate.py` | Database schema migration runner | Active |
| `scripts/generate_gap_predictions.py` | Elo hybrid h2h predictions for club gap fixtures (no Standings row). `--dry-run` flag available | Active |
| `scripts/update_national_ratings.py` | Rebuild `pool='national'` Elo ratings from the 18-competition whitelist; prints isolation report | Active |
| `scripts/generate_wc_predictions.py` | National Elo predictions for World Cup NS fixtures (`data_context=national_elo`); UPDATE not INSERT | Active |
| `scripts/check_model.py` | Inspect trained model metadata | Diagnostic tool |
| `scripts/diagnostics.py` | Connectivity checks, backfill config validation | Diagnostic tool |
| `scripts/daily_sanity_check.py` | Sanity checks run by scheduler | Active |
| `scripts/capture_forward_odds.py` | Capture open→close odds time-series for forward-collection leagues (Pinnacle + Bet365 only) | Active |
| `scripts/probe_forward_odds.py` | One-shot bookmaker-detection probe: fetches raw odds for a given `--league-ids` list, logs ALL bookmaker names and raw `bet_name` strings, writes to `odds_snapshots` only if Pinnacle present, writes `logs/soft_book_decision_needed.txt` flag if only soft books found | Active |
| `scripts/auto_bet.py` | Legacy betting pipeline — **DEPRECATED** (not in live path) | Dead — kept for reference |
| `scripts/live_monitor.py` | Watch live matches in real-time | Likely dead |
