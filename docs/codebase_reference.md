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
│   ├── backtesting/   Historical simulation framework
│   ├── betting/       Kelly, portfolio machinery, execution engine — V1-only, still required by the still-running coordinator.py/web_ui.py until Part D's cutover (Phase 31: prediction.py/market_taxonomy.py/league_normalizer.py/temporal_adapter.py/ev.py/shin.py moved out to prediction/lib/ in D4; confidence_weighting.py/markets.py/risk_decisions.py/unified_latent.py/latent_shock.py/stress_testing.py/portfolio_optimizer.py/capital_allocator.py/market_feasibility.py archived to V1_archive/ in D7b as confirmed-safe-now; alerts.py entangled but inert — see OWNERSHIP.md)
│   ├── calibration/   State calibration engine, calibrator fitting (Phase 31: fit_calibrator_for_market relocated in from backend/execution_engine.py)
│   ├── cache/         Prediction caching
│   ├── cli/           Command-line tools (backtest, event replay)
│   ├── contracts/     Pipeline stage data contracts
│   ├── evaluation/    Model metrics (Brier score, Sharpe, calibration)
│   ├── events/        Event-driven architecture (types, store, routing)
│   ├── features/      Elo (form.py/strength.py/xg_features.py archived to V1_archive/dead/ in Phase 31 D7 — zero live importers, orphaned feeders for the archived per-market models below)
│   ├── governance/    Policy engine, closed-loop validation — V1-only, pending Part D archive (Phase 31: runtime_lock.py/system_versioning.py/lineage_tracker.py moved out to infra/, the only genuinely generic modules in the directory)
│   ├── infra/         Phase 31 Part D: RuntimeLock (single-instance guard), run versioning/lineage tracking — relocated out of governance/, which otherwise means policy/CLVE/meta-policy
│   ├── ingestion/     API-Football v3 client, backfill pipeline
│   ├── learning/      Feedback loop, weight optimizer, event replay
│   ├── maintenance.py Database cleanup utilities
│   ├── models/        ML model training, calibration, registry, drift detection (Phase 31 D7: the 9 orphaned per-market statistical models — btts/dixon_coles/ensemble/h2h/halftime/injuries/late_goals/overunder/poisson — archived to V1_archive/dead/, zero live importers)
│   ├── notifications/ Discord notifier, agent reporter
│   ├── performance/   Performance metrics tracker
│   ├── portfolio/     Self-optimizing and adaptive allocators
│   ├── prediction/    Unified prediction service, market normalizer, prediction cycle; lib/ — feature engineering + EV math relocated from betting/ in Phase 31 (prediction.py, market_taxonomy.py, league_normalizer.py, temporal_adapter.py, ev.py, shin.py)
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
| `python backend/runtime/v2_runtime.py` | **Sole execution authority since Phase 31 Part D (D10) cutover** — V2 execution process (`bootball-v2-runtime.service`), runs `src.prediction.prediction_cycle.run_prediction_cycle()` every 20 minutes, `V2_RUNTIME_WRITE_ENABLED=true` (saves predictions, runs calibration ingest), and owns `backend/scheduler.py`'s auxiliary APScheduler; holds `RuntimeLock` file `data/v2_execution_runtime.lock` — see `OWNERSHIP.md` and `PART_D_PROGRESS.md` |
| `python scripts/migrate.py` | Run database schema migrations |
| `python scripts/backfill_all.py --seasons 2023 2022` | Backfill historical data |
| `python src/cli/backtest.py` | Historical strategy simulation |
| `python src/cli/event_replay.py` | Replay and debug event sequences |

(Phase 31 Part D: `python backend/app.py`, a second unused Flask app-factory with zero
importers anywhere in the live tree, archived to `V1_archive/backend/app.py`.)

### Startup Sequence

Two services run, both managed by systemd, since Phase 31 Part D's D10 cutover
(`bootball-runtime.service` and `bootball-web.service` — V1 — are stopped + disabled; see
`OWNERSHIP.md` and `PART_D_PROGRESS.md`):

```
bootball-web-v2.service  →  scripts/web_ui_v2.py (port 5000, primary)
  1. record_running_commit("bootball-web-v2.service") via src/deploy_info.py
  2. Flask app created; blueprints registered (home, track_a, predictions, collection, explorer)
  3. init_db() via src/storage/db.py
  4. All routes protected by require_auth() from v2/auth_v2.py
     (cookie: authenticated_v2; no V1 cookie collision)

bootball-v2-runtime.service (separate process, sole execution authority since D10):
  1. record_running_commit("bootball-v2-runtime.service") via src/deploy_info.py
  2. backend/runtime/v2_runtime.py → src.prediction.prediction_cycle.run_prediction_cycle()
     (every 20 minutes; V2_RUNTIME_WRITE_ENABLED=true — saves predictions, runs calibration
     ingest)
  3. Acquires RuntimeLock file (data/v2_execution_runtime.lock)
  4. Starts backend/scheduler.py's auxiliary APScheduler — 7 jobs: fetch_fixtures (6h),
     fetch_results (1h), fetch_odds (1h), cleanup_matches (5m), live_settle (2m),
     daily_sanity_check (24h), v2_collection_heartbeat — code landed D9, activated at D10's
     cutover restart (2026-07-07) once bootball-runtime.service stopped owning it
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
| `config/settings.py` | All env-based settings: API keys, scheduling, model dirs, runtime mode; `backfill_daily_cap` (default 60 000) soft-caps backfill quota; `collection_daily_cap` (default 15 000, Phase 25) caps `odds_trajectory_scheduler.py` daily-phase spend; leagues 777/778/779/648 added to `calendar_year_leagues`; `get_season()` resolution order (Phase 29) is `shifted_label_leagues` → `late_rollover_leagues` → `calendar_year_leagues` → default European `month>=7` — see "New-Season Readiness" in `deployment_state.md`; `discord_v1_enabled` (default `False`, Phase 30) gates V1's retired Discord notifier — see "The Separation Principle" |
| `config/leagues.py` | `ALL_LEAGUE_IDS` — 1,225 leagues; league metadata; season definitions |
| `config/forward_leagues.py` | Narrow 4-5-league forward-collection config (Pinnacle-covered, high goal-rate); capture bookmakers (Pinnacle, Bet365); market types (h2h, o/u 2.5, BTTS); stale-window constant — **superseded (Phase 25)** by `odds_trajectory_scheduler.py`, which covers all leagues |
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

- **7 registered auxiliary jobs:** `job_fetch_fixtures` (6h), `job_fetch_results` (1h), `job_fetch_odds` (1h), `job_cleanup_matches` (5m), `job_live_settle` (2m), `job_daily_sanity_check` (24h), `job_v2_collection_heartbeat` (24h)
- `job_fetch_results` now calls `src.settlement.verify_ft_fixtures()` (Phase 27) right before `settle_all()`, so reversible markets never settle off an unconfirmed FT snapshot
- Phase 31 Part D: removed `job_auto_heal_runs`, `job_retrain_models`, `job_run_betting_bot`, `job_run_continuous_cycle`, `MUTATING_JOBS`, and `is_job_allowed_in_mode()` — none of the four job functions were ever wired into the registered list above (confirmed live), and all four only existed to dispatch into V1's `ExecutionEngine`/`AgentCoordinator`/`run_continuous_cycle.py`, which Part D archives. The mode-gating function existed solely to guard these four job types.
- `_circuit_ok()` / `_circuit_failure()` — fault-tolerant job execution

### `backend/runtime/execution_runtime.py`

**V1, retired (Phase 31 Part D, D10 cutover, 2026-07-07).** `bootball-runtime.service` is
stopped + disabled; `backend/runtime/v2_runtime.py` is the sole execution authority now — see
`OWNERSHIP.md`/`PART_D_PROGRESS.md`. Left in place for reference pending D7c's dependent-archival
pass. What it used to do:

- Ran as a separate process from the Flask web UI
- Called `AgentCoordinator.run_cycle()` every 1200 seconds (20 minutes)
- `RuntimeLock` enforced single-instance operation (prevents concurrent cycles)
- Heartbeat watchdog updated every 60s during sleep
- Was the entry point for all betting activity; APScheduler only handles data-fetch auxiliary jobs
- `_run_settlement()` called `src.settlement.verify_ft_fixtures()` (Phase 27) before `settle_placed_bets()`/`settle_predictions()`

### `backend/experiment_tracker.py`

Tracks experiment runs end-to-end (singleton).

- `SystemSnapshot` — captures model versions, config hash, run metadata
- `ExperimentTracker.start_run()`, `capture_system_snapshot()`, `finalize_run()`
- Writes to `experiment_runs` table; run artifacts viewable at `/runs/{run_id}`

### `backend/causal_graph.py`

Graph-based decision explainability.

- `DecisionNode`, `DecisionEdge`, `CausalGraph`
- Tracks decision influences for audit trail

---

## Source Module Reference

### `src/deploy_info.py`

Deployment state tracking — records which git commit a long-running service started from.

- `record_running_commit(service_name)` — called once at startup by `v2_runtime.py` and `web_ui_v2.py`; writes current HEAD commit to `logs/deploy_state/<service_name>.running_commit`
- Allows `scripts/deploy.sh check` to detect stale services without correlating systemd timelines against git log
- Non-fatal if git unavailable; designed to survive any restart method (deploy script, manual `systemctl restart`, host reboot)

### `src/storage/db.py`

SQLAlchemy database layer.

- `get_engine()`, `get_session_maker()`, `init_db()`
- `get_session()` — context manager returning a scoped session
- SQLite connections set `PRAGMA busy_timeout = 5000` (Phase 25) — 5+ independent writer processes share this file; without it, two writers colliding get an immediate `SQLITE_BUSY` instead of a short wait

### `src/storage/models.py`

40+ SQLAlchemy ORM models covering the full football domain.

Key models:

| Model | Key Fields |
|-------|-----------|
| `Fixture` | id, league_id, home_team_id, away_team_id, date, status, goals_home, goals_away, ft_verified_at (Phase 27 — set once a force-refetch confirms FT/AET/PEN; gates reversible-market settlement, see `src/settlement.py`) |
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
| `CalibrationDriftState` | market (PK), last_seen_prediction_id, updated_at — Phase 28, persistent dedup for the live-drift monitor (replaces the in-memory set that replayed frozen outcomes on every restart) |
| `EloRebuildLog` | pool, invoked_at, invoked_by, fixtures_processed, latest_fixture_ceiling — Phase 28, one row per `update_all_ratings()` call |

### `src/agents/coordinator.py`

**Central execution spine.** Orchestrates the full prediction-to-execution pipeline.

Pipeline stages:
```
Predictor → Risk Manager → Execution Strategist → Portfolio Engine
  → Adversary (stress test) → Policy Engine → Save Bets
  → Feedback Loop → CLVE validation
```

- `AgentCoordinator.run_cycle()` — was the primary entrypoint, called by `ExecutionRuntime` every 20 minutes; V1 retired at D10, no live caller remains
- `AgentCoordinator.run()` — thin wrapper that delegates to `run_cycle()`
- `_write_attribution()` — writes causal attribution for each decision

**Phase 31 finding, cut over at D10 (2026-07-07) — see `OWNERSHIP.md`:** of this file's
~1050-line `_run_internal()`/`_run_feedback_cycle()`, only two things ever had a live effect
since betting closed (Phase 8, `bot_enabled=False`): the prediction-generation call (Step 1) and
the live-drift calibration ingest buried in the feedback cycle (Step 7.1/7.3). Everything else —
Risk Manager, Execution Strategist, Portfolio Engine, Adversary, Policy Engine, the `PlacedBet`
write block, Learning/WeightOptimizer/EventReplay, Meta-Policy, CLVE — ran every cycle against a
betting ledger that took zero new rows since 2026-06-07. `src/prediction/prediction_cycle.py` is
the lean V2 replacement, now the sole live caller of both surviving effects; this file itself is
dead code pending D7c's archival pass.

### `src/prediction/prediction_cycle.py`

**The V2-owned replacement for `AgentCoordinator.run_cycle()`'s live core** — wired into
`bootball-v2-runtime.service` as the sole execution authority since Phase 31 Part D's D10
cutover (2026-07-07). Contains exactly what has a live effect: fetch NS fixtures, generate +
save predictions, then run the live-drift calibration ingest that `AgentCoordinator` was
previously the sole caller of.

- `generate_predictions(save=True, run_id=None)` — fetches NS fixtures, calls
  `UnifiedPredictionService.generate_with_fixture_data()`/`save_predictions()`; `save=False` was
  used for pre-cutover parity verification against `AgentCoordinator`'s output, writes nothing
- `run_calibration_ingest()` — calls `state_calibration_engine.ingest_recent_prediction_outcomes()`
  and, if there were new outcomes, `.generate_report()` (fires `CALIBRATION_DRIFT_DETECTED`)
- `run_prediction_cycle(save=True, run_id=None)` — the full cycle: both of the above together

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

### `src/betting/kelly.py`

Kelly criterion stake sizing.

- `kelly_fraction(p, b)` — full Kelly
- `fractional_kelly(p, b, fraction)` — risk-reduced Kelly
- `kelly_stake(bankroll, p, b, fraction, cap)` — final stake with bankroll cap

### `src/prediction/lib/ev.py`

Expected value and implied probability. (Phase 31 Part D: relocated from `src/betting/ev.py` —
live production math living under a "betting" path was exactly the naming lie this phase exists
to end; see `ADOPTION.md`.)

- `expected_value(prob, decimal_odds)` → float
- `implied_probability(decimal_odds)` → float

### `src/prediction/lib/shin.py`

Fair probability extraction from bookmaker odds. (Phase 31 Part D: relocated from
`src/betting/shin.py`, same rationale as `ev.py` above.)

- `shin_probabilities(odds_list)` — removes overround via Shin model
- `overround(odds_list)` — calculates bookmaker margin

### `src/prediction/lib/prediction.py`, `market_taxonomy.py`, `league_normalizer.py`, `temporal_adapter.py`

Relocated from `src/betting/` in Phase 31 Part D (same rationale) — feature engineering and
league/temporal baseline adjustment feeding `unified_prediction_service.py`'s blend step.
`prediction.py::get_model_prediction()` is the actual production prediction loader; it was
previously the most misleading case of the naming problem this phase fixes, since "the real
prediction code lives in the betting package" was true and confusing.

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
- `verify_ft_fixtures(hours=6, limit=100)` (Phase 27) — force-refetches (batched, 20/call) a fixture once before its reversible markets settle, stamping `Fixture.ft_verified_at`. Root cause: the per-league `status="FT"` completed-fixtures fetch never passed `force_refresh=True`, so a provider glitch or stale read (fixture momentarily reported FT with a mid-match/halftime score) got cached and stayed frozen for the rest of that day — two confirmed cases (2026-07-02) settled h2h off a frozen HT score. `settle_predictions()` now only settles reversible outcomes (h2h always; the FT-only branches of btts/ou — No/Under) once `ft_verified_at` is set; irreversible early-settle outcomes (btts=Yes, ou=Over) are unaffected since they're safe the instant they're mathematically certain. Both per-league `status="FT"` fetches (here and in `daily_run.py::_fetch_completed()`) now pass `force_refresh=True` so a bad snapshot can't freeze all day; `_save_completed()` no longer unconditionally overwrites a fixture's status to `"FT"` if it's already in a terminal state.

### `src/ingestion/client.py`

API-Football v3 client.

- `APIFootballClient` — rate-limited, response-cached client
- `calls_used_today()` / `calls_remaining_today()` — API quota tracking
- `get_api_status()` — live quota from `/status` endpoint (cached 2 min)
- Cache files live at `data/raw/api_cache/api_cache/` (`CACHE_DIR`); cache reads/writes both target this path
- `get_odds()` forces `force_refresh=True` (Phase 25) — odds are time-varying, unlike fixtures/teams/leagues metadata; without this a repoll of an already-seen fixture silently served the first-ever cached response forever

### `src/ingestion/odds_snapshot_capture.py`

Shared `odds_snapshots` writer (Phase 25) used by both `scripts/odds_trajectory_scheduler.py` and the passive piggyback layer in `scripts/odds_poll.py`.

- `write_snapshots_from_response(s, raw_odds, fixture_id, captured_at, dedupe_minutes=45)` — parses one already-fetched `get_odds()` response and inserts a row per bookmaker/market, deduped; returns `{"written", "skipped_dedupe", "skipped_unparsed"}`
- `already_captured_within(s, fixture_id, market_type, bookmaker_name, minutes)` — dedupe check
- Captures every bookmaker in the response, not just Pinnacle+Bet365 (unlike `config/forward_leagues.py`'s narrower `CAPTURE_BOOKMAKERS`)

### `src/security/safe_load.py`

HMAC-SHA256 signed model persistence.

- `safe_model_save(model, path)` — saves model + detached `.sig` file
- `safe_model_load(path)` — verifies signature before loading; raises on tamper

### `src/events/event_bus.py`

System-wide event pub/sub (singleton). (Phase 31 Part D: relocated from `src/alerts/event_bus.py`
into `src/events/`, alongside the rest of the event-driven architecture it was already the hub
of — `src/alerts/` otherwise held only two now-archived, Discord-only dead files.)

- `event_bus` — global event bus instance
- `Events` enum — all registered event types, including `SETTLEMENT_INTEGRITY_EVENT` (Phase 30 — see "The Separation Principle")
- `event_bus.emit(Events.X, payload)` / `event_bus.on(Events.X, handler)`
- `event_bus.emit()` fans out three ways: direct `.subscribe()` handlers, `src/events/consumers/registry.py`'s consumer dispatch, and event persistence — see `src/notifications/v2_discord_notifier.py` (current Discord voice) and `src/notifications/discord_system_notifier.py` (retired V1 voice, `discord_v1_enabled`-gated) for the two Discord consumers of this bus

---

## Key Data Flows

### Fixture → Prediction → Bet → Settlement

```
API-Football
  ↓  job_fetch_fixtures / job_fetch_odds  (APScheduler auxiliary)
fixtures, fixture_odds tables
  ↓  V2ExecutionRuntime → prediction_cycle.run_prediction_cycle()  (every 20 min, since D10)
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
  ↓  Decorator check
@mode_guard([TRAINING, DEV]) on train functions → raises RuntimeError in LIVE
```

(Phase 31 Part D: the scheduler-job mode check shown here previously, `is_job_allowed_in_mode()`,
was removed along with the four job types it existed to gate — see `backend/scheduler.py` above.)

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
- **Phase 28 — rebuild governance**: nothing schedules `update_all_ratings()` for either pool (only `scripts/update_national_ratings.py` calls it, manually, for `national`); the Phase 27 settlement audit found the club-pool rebuild that ran inside the corruption window had no traceable invoker. Every call now writes one row to `elo_rebuild_log` — `pool`, `invoked_by` (caller's file:function, captured automatically via `inspect.stack()`, no call site needs to remember to pass it), `fixtures_processed`, `latest_fixture_ceiling` (max fixture date folded in).

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

**Phase 21 — void unplayable predictions, correct stale dates, prevent recurrence**

`src/settlement.py` — new functions:
- `_outcomes_match(market, predicted_outcome, actual)` / `_H2H_NOTATION` — normalizes h2h notation (`H`/`D`/`A` from the Elo hybrid path vs `1`/`X`/`2` from the ensemble path) before comparing. `settle_predictions()` now uses this instead of a raw string comparison — previously every Elo-family h2h prediction was silently scored as a loss on settlement (bug found dormant: 132 letter-notation records, all still unsettled at discovery, none yet mis-scored in production).
- `VOID_STATUSES = ("PST", "CANC", "ABD", "WO", "SUSP")` and `void_unplayable_predictions(fixture_ids=None)` — marks unsettled predictions for these fixture statuses `settled=True, won=None, actual_outcome=<status>`. `get_track_a_stats()` filters `won.isnot(None)`, so voided rows are excluded from the accuracy denominator rather than scored as losses.
- `settle_awarded_predictions(fixture_ids=None)` — for `AWD` (awarded/walk-over) fixtures: batch-fetches the API's `teams.{home,away}.winner` flag, settles h2h as a normal hit/miss against the awarded winner, voids goal-based markets (ou25/ou15/btts — no goals were actually played). If neither team has a winner flag (rare API data gap), voids h2h too rather than guessing. Never promotes `Fixture.status` away from `AWD`, so the generic FT-goal settlement path can never fire against a forfeit placeholder scoreline.
- `resync_stale_fixtures(limit=100)` — re-fetches (batched, 20/call) fixtures stuck at `status='NS'` with `date < now`, the one state `_save_upcoming()` can never self-correct (it only updates fixtures the API still reports NS within its rolling 7-day window). Diff-then-write on date/status/goals/outcome. Fixtures resolving to FT are left for `settle_predictions()`; PST/CANC/ABD/WO/SUSP trigger `void_unplayable_predictions()`; AWD triggers `settle_awarded_predictions()` — all scoped to just the fixtures resolved in that call.

`scripts/daily_run.py` — new Step 3 (`resync_stale_fixtures(limit=100)`, ~5 API calls/run) runs after fetching upcoming fixtures and before settlement, so any fixtures it resolves to FT/void/AWD are picked up by the same run's settlement step. Steps renumbered 1–7.

`src/ingestion/backfill.py` — `backfill_league_season()` existing-fixture branch (previously a no-op) now diff-then-writes `status/date/goals_home/goals_away/ht_goals_home/ht_goals_away/outcome` when the API's FT data disagrees with the stored row. Removed unused `_fixture_exists()` helper.

One-time cleanup run: voided 105 PST/CANC + 63 AWD goal-market predictions (168 total), settled 17 AWD h2h predictions against the awarded winner (3 AWD fixtures had no API winner flag, voided), resynced 81 stale NS fixtures (35 resolved to FT, 2 to void, 28 had simply not kicked off yet by the API's clock, ~16 fixture IDs no longer exist in the API — untraceable, left unchanged). Total: 9 API calls.

**Phase 22 — dispose of untraceable fixtures; fix stale-service deployment gap in soft odds**

Investigation found the "untraceable" fixtures from Phase 21 (empty API response) are not random old data — every one belongs to a playoff/knockout-bracket competition (`Liga III - Play-offs` Romania, `Segunda División RFEF - Play-offs` Spain) or a provisional lower-tier/youth/women's schedule (Eredivisie/Eredivisie Women, Gabon `Championnat D1`, `U19 Divisie 1`). Re-querying the same league/date via the list endpoint (not `id=`) shows the API-Football provider re-issued the round with brand-new fixture IDs and, in several cases, entirely different team pairings (e.g. stored "Mangasport vs Vautour Club" (id 1533202) → live API now has "Mangasport vs Lozo" (id 1554020)) — the provider replaces provisional/bracket fixtures once the real matchup is finalized, orphaning the old ID permanently. This is a **recurring provider behavior**, not one-off historical residue — it will keep happening for any competition with provisional bracket scheduling.

`src/settlement.py`:
- `DEAD_THRESHOLD = 3` / `_load_stale_failures()` / `_save_stale_failures()` — JSON counter file (`data/raw/.stale_fetch_failures.json`, same pattern as `client.py`'s call counter) tracking consecutive empty-API-response misses per fixture ID across `resync_stale_fixtures()` calls. Reset to zero on any successful response.
- `mark_fixtures_dead(fixture_ids)` — sets `Fixture.status='DEAD'` (excludes the fixture from `resync_stale_fixtures()`'s `status='NS'` query permanently) and voids unsettled predictions with `actual_outcome='untraced'` (kept ≤10 chars — `actual_outcome` is `String(10)`; SQLite doesn't enforce the length but Postgres/MySQL would).
- `resync_stale_fixtures()` now diffs requested IDs against the batch response; any ID absent from the response increments its failure counter, and IDs reaching `DEAD_THRESHOLD` are auto-marked dead within the same call — no manual cleanup needed for future occurrences. Guarded against conflating a call-level failure (quota exhaustion, network blip — entire batch empty) with genuine per-ID 404s: misses are only counted when at least some IDs in the batch resolved. Return dict gained a `marked_dead` count.

One-time cleanup: 16 already-confirmed-untraceable fixtures (verified via repeated force-refresh probes) marked `DEAD` directly, voiding 61 predictions. Resync queue dropped from 42 to 26 (later 24 as new fixtures naturally passed kickoff).

**Deployment gap found in the backend, not just the web tier.** `bootball-runtime.service` (APScheduler, in-process — `backend/scheduler.py`'s `job_fetch_fixtures`/`job_fetch_results` call `DailyBaselinePipeline().run()` directly inside the long-lived interpreter, every 1–6 hours) had also been running since before today's commits. Python's module cache means its deferred `from src.settlement import ...` calls were still resolving to the pre-Phase-21/22 module — the notation fix, void/resync/auto-dead functions were committed but not executing anywhere. Restarted `bootball-runtime.service` in addition to `bootball-web-v2.service` so both the pipeline and the UI serve current code.

**Soft odds — root cause was a stale, unrestarted service, not a code defect.** `bootball-web-v2.service` (systemd, port 5000) had been running since 07:25 UTC — 5+ hours before the Phase 18 commit (12:39 UTC) landed. Flask's `app.run(debug=False)` does not hot-reload, so the live process was still serving pre-Phase-18 code: bare probabilities in the market columns (`H 38% D 30% A 33%`, no prices) and a stray soft-book price detached from any market crammed into the Track B cell (`10Bet 1.53 · No sharp odds`) — exactly Phase 15's original problem. Verified the data layer (`get_predictions_for_upcoming()`) and rendering (`_format_market()`) were already correct by calling them directly in a Python shell before touching the service. `systemctl restart bootball-web-v2.service` made the Phase 18 code live; confirmed via `curl` against the running port that both views now render per-outcome prices (e.g. `H 38% (2.15) D 28% (3.20) A 34% (3.70) Bet365`) with Track B showing only the Pinnacle verdict.

A second, real gap was found and fixed during verification: `v2/routes/explorer_v2.py`'s `_mkt_cell()` h2h **scalar-fallback** branch (used when the full H/D/A vector isn't stored, only `predicted_outcome`) never attached a price — unlike `predictions_v2.py`'s equivalent branch. Fixed to look up the outcome-matched price (`soft_home`/`soft_draw`/`soft_away`) the same way the predictions view does. Required a second service restart, since the first restart predated this fix.

---

## The Separation Principle (Phase 28)

Governs Track A (prediction accuracy, scored on outcomes regardless of odds) and Track B (EV/CLV overlay, Pinnacle-gated, analytical only since the betting thesis closed at Phase 8):

> - **The prediction layer learns ONLY from match reality:** fixtures, scores, settled outcomes via PredictionRecord / Track A. Its models and calibrators are trained, refit, and drift-monitored exclusively on that data.
> - **The (future) betting layer consumes predictions read-only.** It selects its bets FROM the real predictions, but it has its own "did I do well" structure — its own betting-choice and evaluation logic, its own state, its own metrics (ROI, CLV, selection quality), its own models if needed.
> - **Betting NEVER feeds back into the prediction models or their calibration.** Predictions are built on facts; betting uses those facts to make its own reality, and its feedback loops stay entirely inside the betting layer. One-way flow: match reality → predictions → betting selection → betting self-evaluation. No arrow points back.

**Case study — the ghost alarm (Phase 27b/28).** Two unrelated metrics were both called "ECE," and the confusion is exactly the kind of arrow this principle forbids:

- `live_drift_ece` (`StateCalibrationEngine.compute_calibration_metrics()`) — the drift monitor. Fires `CALIBRATION_DRIFT_DETECTED`, which triggers a live recalibration of the market's calibrator.
- `postfit_eval_ece` (`ModelVersion.ece`, from `src/calibration/calibrator_fitting.py::fit_calibrator_for_market()`, relocated from `backend/execution_engine.py` in Phase 31 Part D) — a specific calibrator's own held-out post-fit eval score. Healthy: near-zero through May 2026, 0.05–0.13 since mid-June.

Before Phase 28, the drift monitor's input was `AgentCoordinator._run_feedback_cycle()` pulling the 100 most recent settled `PlacedBet` rows — a betting-layer artifact — into `live_drift_ece`'s computation. Betting closed 2026-06-11 (Phase 8); those 100 rows never changed again. The in-memory dedup (`_calibration_seen_bet_ids`) reset on every process restart, so the same 25 frozen h2h bets replayed as "new" forever, recomputing the identical `live_drift_ece=0.2807167287008361` on every restart and firing 94 pointless h2h recalibrations — a betting-layer number silently driving prediction-layer retraining, the last live arrow pointing backward. `postfit_eval_ece` was fine the whole time (0.05–0.13); the alarm reading a dead, disconnected pipeline is what never went away.

Phase 28 retargeted `live_drift_ece` to read newly-settled `PredictionRecord` rows only, with the dedup moved to a persistent per-market high-water mark (`calibration_drift_state` table) so a restart resumes instead of replaying. The old `PlacedBet`-reading code is preserved, unused, at `AgentCoordinator._fetch_placed_bet_outcomes_LEGACY_UNUSED()` — reference for whenever betting is rebuilt, at which point it gets its own layer with its own state, not a resurrection of this arrow.

**Going forward:** any new metric, retrain trigger, or feedback loop touching prediction models or calibrators must be checked against this principle before it ships — if its input can be traced back through a betting-domain table (`PlacedBet`, bet P&L, ROI), it's the wrong input.

**Phase 30 — silence V1's Discord, give V2 an honest voice; the unblended-EV data defect.**

V1's coordinator-cycle Discord notifier (per-market picks with Kelly/EV, "Top 3 Picks", `POLICY ENGINE REPORT`, `Cycle Complete`, Adaptation Score / Closed Loop Validation theater) is now silenced by default — betting-era machinery keeps orchestrating (it writes production `PredictionRecord`s via `UnifiedPredictionService`), but it no longer has a live Discord voice. Gated by `settings.discord_v1_enabled` (`config/settings.py`, default `False`) at three independent choke points that previously each read `DISCORD_WEBHOOK_URL` and sent unconditionally:
- `src/notifications/discord_system_notifier.py::_post()` — the single point every function in that module (and `model_registry.py`'s direct `notify_model_change()` calls) funnels through.
- `src/events/bootstrap.py::bootstrap_consumers()` — `DiscordConsumer`, `PolicyConsumer`, `CLVEConsumer` (pure-notification consumers) are only registered when the flag is on.
- `src/betting/alerts.py::DiscordChannel.send()` — the V1 betting-alert path (bet/settlement/bankroll pings), dormant since betting closed at Phase 8 but still wired into `settle_placed_bets()`.

One coupling bug found and fixed along the way: `src/events/consumers/calibration_consumer.py`'s `process()` gated its *entire* handling of `CALIBRATION_DRIFT_DETECTED` — including the real auto-recalibration it triggers via `ModelRegistry`, not just the Discord report — behind "webhook configured." Recalibration is a prediction-layer action and must never depend on Discord config; the gate was moved down to `_send_webhook()` only, and `CalibrationConsumer` now always registers.

**The +134% EV was a data defect, not a display bug.** `UnifiedPredictionService._get_market_odds_set()` (`src/prediction/unified_prediction_service.py`) fetched `FixtureOdds` rows inside a `with get_session()` block but read their attributes *after* the block exited — since `get_session()`'s sessionmaker uses the SQLAlchemy default `expire_on_commit=True`, the commit-on-exit expired every attribute, and the subsequent read raised `DetachedInstanceError` on every single call where odds existed. The caller's blanket `except Exception` swallowed it silently and fell back to the unblended `calibrated_prob` for EV — meaning `blend_with_market()` (the Shin de-vig shrink described in `src/calibration/market_blend.py`) had **never once executed successfully in production**: 100% of the 11,511 odds-bearing `prediction_records` rows carried the unblended signature (`market_prob IS NULL`), including every row the "Phase 15" `notify_top_picks()` fix was supposedly reading a genuinely-blended `pr.ev` from. Fixed by moving the attribute reads inside the `with` block. `generate_with_fixture_data()`'s blend step is now its own try/except with loud `logger.error`/`logger.warning` and a per-cycle `_blend_fallback_count` summary — a blend fallback can no longer be silent. A one-time backfill (ad hoc, not a committed script) recomputed `ev`/`blended_prob`/`market_prob` for the 160 unsettled, still-upcoming odds-bearing rows that predated the fix (159 updated, 1 left unblended — genuinely incomplete odds set); `predicted_outcome`/`our_prob`/`calibrated_prob` were left untouched, and settled history was deliberately not rewritten. **Era boundary:** any `prediction_records.ev` written before this fix (all settled history, plus any row not touched by the backfill) carries the unblended signature and should not be treated as market-blended EV.

**A second, independent unblended-EV write path** was found the same way: `scripts/odds_poll.py::recalculate_prediction_ev()` (invoked by `backend/scheduler.py`'s `job_fetch_odds`, in-process inside `bootball-runtime.service`) recomputed `pred.ev` straight off `calibrated_prob` with no market blend attempted at all, and ran on every odds change — silently overwriting a correctly market-blended `ev`/`blended_prob`/`market_prob` with an unblended one each time odds moved. Fixed to call the same `blend_with_market()` shrink via a local `_market_odds_set()` helper (mirrors `_get_market_odds_set()`, built the correct way — read while the session is still open), with the same loud-not-silent fallback logging. A residual, separate, lower-severity bug was found but *not* fixed (out of scope for this pass): `recalculate_prediction_ev()`'s single-bookmaker `odds_row = ....first()` lookup can land on a bookmaker row with every odds column `NULL` (some rows exist per-fixture per-bet-type with no actual prices, e.g. a book that didn't quote that market) instead of aggregating across bookmakers like `_get_odds_for_market()`/`_market_odds_set()` do — causing it to skip updating a handful of predictions (~40 of 298 unsettled odds-bearing rows at time of writing) that do have valid odds from a different bookmaker.

**A third, subtler gap in the same fix** surfaced during Phase 31's inventory re-audit: `generate_with_fixture_data()`'s fallback check only tested whether `market_odds` came back truthy from `_get_market_odds_set()` — it never checked whether `blend_with_market()` itself then internally rejected the blend (outcome label mismatch, odds < 1.01, Shin failure) and returned `p_market=None` anyway, which writes unblended EV with zero log trace. `odds_poll.py`'s equivalent already checked this correctly (`if p_market is None:`); `unified_prediction_service.py` did not. Fixed to check `p_market is None` after the blend attempt, not just `market_odds` truthiness. Reconciled live: of 63 unsettled odds-bearing rows sitting unblended, 27 were still pre-kickoff (NS, future) and got a clean regenerate+save; the other 36 were already live/finished but not yet settled and were deliberately left untouched (they'll settle with whatever `ev` was last computed before their own kickoff, per the recompute-forward-only-when-pre-kickoff rule).

**V2's Discord voice** (`src/notifications/v2_discord_notifier.py`) is wired unconditionally (independent of `discord_v1_enabled`) and posts under a distinct `Bootball V2` username on the same shared webhook relay (confirmed generic at Phase 15) so provenance is never ambiguous. Four events, no predictions/picks/EV/Kelly ever:
- `notify_drift_alarm()` — subscribed to `Events.CALIBRATION_DRIFT_DETECTED`; 6h per-market cooldown.
- `notify_settlement_integrity()` — subscribed to new `Events.SETTLEMENT_INTEGRITY_EVENT`, emitted from three points in `src/settlement.py`: `verify_ft_fixtures()` FT-snapshot corrections, `update_pending_fixture_scores()` forward-dated-live catches, and a new `_check_dead_mark_spike()` in `mark_fixtures_dead()` — a self-computed trailing rolling baseline (no hard-coded number existed) persisted to `data/raw/.dead_mark_history.json`; 15min per-kind cooldown.
- `notify_collection_heartbeat()` — new daily APScheduler job (`backend/scheduler.py::job_v2_collection_heartbeat`, 24h interval) reporting `odds_snapshots` counts and quota headroom from `logs/quota_log.csv`.
- `notify_deploy_complete()` — called from `scripts/deploy.sh` after a restart cycle with commit hash + per-service active status, deduped by commit.

Rate-limiting and restart-safety both come from one JSON state file (`data/state/v2_notifier_state.json`, last-sent timestamp per event kind + last-notified deploy commit) — read from disk rather than memory, so a process restart cannot replay an event that already fired.

---

## Architecture Patterns

| Pattern | Where Used |
|---------|-----------|
| Singleton | `RuntimeModeManager`, `ExperimentTracker`, `ModelRegistry`, `event_bus`, `LeagueCalibrationEngine`, `ExecutionEngine` (now only `src/betting/execution_engine.py` — `backend/execution_engine.py`'s same-named class was archived in Phase 31 Part D; this one is V1-only too, moving to `V1_archive/` in Part D's next step) |
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
| `tests/integration/test_safe_load.py` | Model signing/loading security |
| `tests/models/test_calibration.py` | Platt calibration |
| `tests/models/test_drift.py` | Drift detection |
| `tests/security/test_validation.py` | Input validation |
| `tests/web_ui/test_predictions_api.py` | Prediction API endpoints |

(Phase 31 Part D: `tests/test_betting.py`, `tests/integration/test_portfolio_optimizer.py`,
`tests/integration/test_policy_constraints.py` archived to `V1_archive/tests/` alongside the
V1 betting-thesis code they tested.)

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
| `scripts/web_ui.py` | V1 Flask UI + APScheduler, port 5001 (via gunicorn in `bootball-web.service`) | **Retired (D10 cutover, 2026-07-07)** — service stopped + disabled, port 5001 dark |
| `scripts/deploy.sh` | Post-commit deployment orchestrator; restarts all long-running services and verifies they start with current commit; `check` subcommand reports staleness without restarting | Active |
| `scripts/daily_run.py` | Data pipeline only (no prediction/betting); enforces `backfill_daily_cap` in `_fetch_completed()`; logs per-run quota snapshots to `logs/quota_log.csv`; `_fetch_completed()`'s per-league `status="FT"` fetch now `force_refresh=True` and `_save_completed()` won't clobber a fixture already in a terminal status (Phase 27); `_force_settlement_baseline()` calls `verify_ft_fixtures()` before settling | Active |
| `scripts/backfill_all.py` | Historical data ingestion (multi-season) | Active |
| `scripts/backfill_cron.py` | Nightly incremental backfill (4am cron) | Active |
| `scripts/backfill_odds.py` | Odds-specific backfill | Active |
| `scripts/backfill_standings.py` | Standings-specific backfill | Active |
| `scripts/odds_poll.py` | Poll odds; recalculate EV; also passively piggybacks `odds_snapshots` writes onto its own fetches (Phase 25, zero extra API cost) | Active |
| `scripts/odds_trajectory_scheduler.py` | Active `odds_snapshots` capture for ALL odds-carrying fixtures (Phase 25) — ~1 touch/day until 6h before kickoff then ~hourly; near-kickoff never subject to `collection_daily_cap`; per-fixture attempt tracking in `logs/trajectory_last_attempt.json`; flock-serialized runs | Active |
| `scripts/migrate.py` | Database schema migration runner | Active |
| `scripts/generate_gap_predictions.py` | Elo hybrid h2h predictions for club gap fixtures (no Standings row). `--dry-run` flag available | Active |
| `scripts/update_national_ratings.py` | Rebuild `pool='national'` Elo ratings from the 18-competition whitelist; prints isolation report | Active |
| `scripts/generate_wc_predictions.py` | National Elo predictions for World Cup NS fixtures (`data_context=national_elo`); UPDATE not INSERT | Active |
| `scripts/check_model.py` | Inspect trained model metadata | Diagnostic tool |
| `scripts/diagnostics.py` | Connectivity checks, backfill config validation | Diagnostic tool |
| `scripts/daily_sanity_check.py` | Sanity checks run by scheduler | Active |
| `scripts/capture_forward_odds.py` | Capture open→close odds time-series for the narrow 4-5-league forward-collection (Pinnacle + Bet365 only) | **Superseded (Phase 25)** by `odds_trajectory_scheduler.py`; never wired into cron, kept for reference |
| `scripts/probe_forward_odds.py` | Tasmania/Norway clock-start check for the same `--league-ids`/`--days-ahead` cron entries — now **read-only** (Phase 25): reads what `odds_trajectory_scheduler.py` already captured and reports Pinnacle presence near kickoff, instead of fetching odds itself | Active |
| `scripts/verify_v2_parity.py` | Read-only dry-run comparison of `src/prediction/prediction_cycle.py`'s output against the most recent stored `PredictionRecord` for the same fixture/market; used pre-D10 to verify the V2 runner before cutover; writes nothing | Reference (cutover complete) |

(Phase 31 Part D: `scripts/auto_bet.py` (dead-but-cron-executing daily, cron line
removed in Part D), `scripts/live_monitor.py`, `scripts/backtest.py`,
`scripts/analysis/walk_forward_backtest.py`, and `src/evaluation/backtesting.py`
archived to `V1_archive/` — all confirmed zero live importers.)
