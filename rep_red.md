# Bootball Refactor Audit — rep_red.md
**Date:** 2026-05-06  
**Scope:** Full codebase. No code changed.  
**Legend:** 🔴 High / 🟡 Medium / 🟢 Low severity

---

## Executive Summary

The codebase has grown through layered rewrites without retiring previous implementations. The result is six separate portfolio optimization paths, three copies of `get_market_result()`, two execution engines with overlapping names, a 8,106-line monolithic web file, and roughly 13 orphaned scripts. The live execution path itself is sound; the waste is accumulated alongside it.

---

## 1. Live Execution Flow

Understanding what is actually active prevents false positives elsewhere.

```
systemd → backend/runtime/execution_runtime.py
             ↓ every 20 min
         backend/scheduler.py (APScheduler)
             ↓ job dispatch
         backend/execution_engine.py → scripts/daily_run.py
                                     → scripts/auto_bet.py
                                     → src/models/trainer.py
             ↓ betting pipeline
         src/agents/coordinator.py
             ↓ multi-agent cycle
         src/agents/predictor/agent.py
         src/agents/risk_manager/agent.py
         src/agents/execution_strategist/agent.py
             ↓ Markowitz portfolio
         src/betting/portfolio/portfolio_engine.py
         src/betting/portfolio/markowitz_optimizer.py  (primary)
         src/betting/portfolio/cvxpy_optimizer.py      (fallback)
             ↓ governed execution
         src/betting/execution_engine.py
         src/governance/policy_engine.py
             ↓ settlement
         src/settlement.py
             ↓ UI
         scripts/web_ui.py (Flask, 8 106 lines)
```

Everything outside this path is either support tooling, legacy, or dead.

---

## 2. Duplicate / Triplicated Logic

### 2.1 🔴 `get_market_result()` — three copies

| File | Line | Status |
|---|---|---|
| `src/settlement.py` | 19 | **Authoritative** — used by all active settlement |
| `scripts/settle_bets.py` | 35 | **Dead copy** — file not called from pipeline |
| `scripts/auto_bet.py` | 286 | **Private variant** (`_get_market_result`) — duplicates same logic |

All three implementations resolve the same market strings (`h2h`, `btts`, `ou25`, `ou15`) from fixture score fields. `auto_bet.py` should import from `src/settlement.py`; `settle_bets.py` should be removed entirely (see §3.1).

---

### 2.2 🔴 Portfolio optimization — six implementations

The live path uses (2) + (3). The rest are partially wired or orphaned.

| # | File | Lines | Status |
|---|---|---|---|
| 1 | `src/betting/portfolio/portfolio_engine.py` | 515 | **ACTIVE** — primary orchestrator, called by coordinator |
| 2 | `src/betting/portfolio/markowitz_optimizer.py` | 425 | **ACTIVE** — QP via SCS solver |
| 3 | `src/betting/portfolio/cvxpy_optimizer.py` | 241 | **ACTIVE** — fallback QP with OSQP/ECOS |
| 4 | `src/betting/portfolio_optimizer.py` | 530 | **SEMI-ACTIVE** — separate `optimize_portfolio()` entry point with correlation avoidance; not called from coordinator |
| 5 | `src/portfolio/adaptive_allocator.py` | 153 | **ACTIVE** — used by portfolio_engine for market-performance weights |
| 6 | `src/portfolio/self_optimizing_allocator.py` | 214 | **ORPHANED** — `get_self_optimizing_allocator()` defined, never imported anywhere |

The dual optimizer path (2 + 3) is intentional (fallback chain) but poorly documented. The duplication problem is that `portfolio_optimizer.py` (4) and `portfolio_engine.py` (1) both implement candidate preparation and correlation-avoidance logic independently, serving the same conceptual role.

**Specific duplicate logic between (1) and (4):**
- `_prepare_candidates()` / `optimize()` — both filter by EV > 0, apply correlation penalties
- `_apply_market_diversification()` in (4) mirrors the new per-market cap added to (2)

---

### 2.3 🔴 Two execution engines with the same name

| File | Lines | Role |
|---|---|---|
| `backend/execution_engine.py` | 358 | **Job dispatcher** — routes scheduler jobs to handlers (daily_run, auto_bet, retrain) |
| `src/betting/execution_engine.py` | 330 | **Governed bet placer** — validates via PolicyEngine, places bets into DB |

Both are called `ExecutionEngine`. The backend one is a job orchestrator; the src one is the actual bet placement layer. They are not duplicates functionally, but the shared name causes confusion and both files have an `_log_execution()` method.

The backend engine also exposes `enforce_execution_boundary()` and `_fit_calibrator_for_market()` which are imported directly in `scripts/auto_bet.py` and `scripts/web_ui.py` — coupling scripts to an internal backend implementation detail.

---

### 2.4 🟡 Discord notifications — three layers

| File | Role | Status |
|---|---|---|
| `src/alerts/discord.py` | Low-level webhook wrapper | ACTIVE |
| `src/notifications/discord_system_notifier.py` | Formatted embeds for cycle events | ACTIVE |
| `src/events/consumers/discord_consumer.py` | EventConsumer bridge | ACTIVE |

All three are live. They form a layered architecture (consumer → notifier → low-level sender) which is acceptable, but `discord_system_notifier.py` bypasses the event consumer pattern and posts directly in some paths (via `wire_to_event_bus()`), while `discord_consumer.py` subscribes to the same events via the consumer registry. The same event (`bets_generated`, `run_finished`) can trigger both paths simultaneously.

---

### 2.5 🟡 Settlement scripts — primary + legacy

| File | Lines | Status |
|---|---|---|
| `src/settlement.py` | 898 | **Authoritative** — full implementation |
| `scripts/settle_fixtures.py` | 112 | **Thin wrapper** — calls `src/settlement.settle_all()` |
| `scripts/settle_bets.py` | 327 | **Dead legacy** — uses `ValueBet` DB model (removed from `storage/models.py`) |

`settle_bets.py` imports `from src.storage.models import ValueBet` at line 24 — this model no longer exists in `models.py` (replaced by `PlacedBet`). The file will crash on import. It also calls `auto_bet._get_market_result()` at line 273, a private function from a different script.

---

### 2.6 🟡 Bankroll management — two parallel systems

| File | Role | Caller |
|---|---|---|
| `src/betting/bankroll.py` (164 lines) | `BankrollManager` — tracks current round, reserves stakes | `src/betting/execution_engine.py`, `src/agents/coordinator.py` |
| `src/agents/shared/state_store.py` (139 lines) | `StateStore._current_bankroll` — separate bankroll float | Markowitz optimizer via execution strategist |

Both track the current bankroll independently. The coordinator now syncs them (added recently), but the fact that two independent stores exist creates the risk of drift again if the sync path breaks.

---

### 2.7 🟢 Maintenance naming collision

| File | Purpose |
|---|---|
| `src/maintenance.py` | Automated cleanup — fixes null goals, orphaned fixtures (called by runtime) |
| `scripts/maintenance.py` | Manual diagnostic — connectivity checks, backfill config validation |

Not a true duplicate (different purposes) but identical names across different directories. A new developer reading a traceback will be confused about which file fired.

---

## 3. Orphaned / Dead Scripts

These files have zero imports from any other project file and are not referenced in the scheduler. They can only be run manually and are not part of any documented workflow.

### 3.1 🔴 Confirmed dead — remove

| File | Lines | Why dead |
|---|---|---|
| `scripts/settle_bets.py` | 327 | Uses `ValueBet` model (removed from DB); not called anywhere |
| `scripts/daily_run_backup.py` | 413 | Explicit backup copy of `daily_run.py`; diverged (refs `ExperimentTracker` import that differs from main) |
| `scripts/retrain_models_new.py` | 468 | Reimplements trainer; not imported or scheduled anywhere |
| `scripts/train_multi_league.py` | 55 | Orphaned training script; zero external refs |
| `scripts/extensive_logging.py` | ~400 | Debug helper; zero external refs |
| `scripts/live_stats_collector.py` | ~330 | Uses `APIFootballClient` but not wired to scheduler or web UI |
| `scripts/setup_db.py` | ~50 | One-time setup; superseded by `migrations/` |
| `data/scheduler.py` | ~80 | Old APScheduler init code predating `backend/scheduler.py`; not imported |
| `src/ingestion/scheduler.py` | 1 | Comment stub only: `# src/ingestion/scheduler.py - Daily operational pipeline` |

### 3.2 🟡 Likely dead — verify before removing

| File | Lines | Note |
|---|---|---|
| `scripts/check_model.py` | ~300 | Diagnostic tool; not in scheduler; useful for ad-hoc |
| `scripts/evaluate_model_calibrated.py` | ~200 | Imports `dixon_coles` — an unused model |
| `scripts/fetch_logos.py` | ~100 | One-time asset fetcher |
| `scripts/fetch_player_data.py` | ~180 | Fetches player data but no DB table for it |
| `scripts/live_monitor.py` | ~180 | Live match monitor; unclear if used operationally |
| `scripts/send_alerts.py` | ~140 | Sends alerts; superseded by `discord_system_notifier.py` |
| `scripts/migrate.py` | ~180 | Manual migration runner; superseded by `migrations/` SQL files |

### 3.3 🟢 Kept — but explain why

| File | Note |
|---|---|
| `scripts/evaluate_model.py` | Used by web_ui.py (12 refs) — keep |
| `scripts/train_multi_calibrated.py` | 1 reference elsewhere — verify before removing |
| `scripts/backtest.py` | Used for offline analysis; CLI tool — keep |

---

## 4. Unused src/ Modules

### 4.1 🔴 `src/betting/capital_allocator.py` — broken reference

`src/alerts/handlers.py` line 191 imports `from src.betting.capital_allocator import ValueBet`. `ValueBet` in `capital_allocator.py` is a local dataclass (line 46), not a DB model — but `scripts/settle_bets.py` expects it as a DB model. This import still exists in `handlers.py` and will fail if that code path executes.

`capital_allocator.py` itself (401 lines) defines `CapitalAllocator` with raw Kelly allocation logic. It is partially superseded by the Markowitz optimizer. Only `src/alerts/handlers.py` imports it, and only for the `ValueBet` dataclass used to format alerts.

### 4.2 🟡 `src/decision_engine/` — parallel decision system

`src/decision_engine/` (6 files, ~800 lines total) implements a rules-based `DecisionEngine` with an action registry. It is bootstrapped by `src/events/bootstrap.py` (line 69) and self-contained. However, the actual betting decisions are made by `src/agents/coordinator.py` and the Markowitz optimizer — `DecisionEngine` is a separate system running in parallel with no shared state. It handles alerting/operational rules (e.g., threshold breaches), not bet selection.

These are distinct responsibilities, but the naming overlap with "decision engine" vs "execution engine" vs "governance engine" creates a confusing landscape.

### 4.3 🟡 `src/models/` — dormant model types

The active model pipeline uses GradientBoostingClassifier trained in `src/models/trainer.py`. These model files exist but are unused in the live pipeline:

| File | Lines | Status |
|---|---|---|
| `src/models/dixon_coles.py` | — | Only used in `scripts/evaluate_model_calibrated.py` (itself orphaned) |
| `src/models/elo_predictor.py` | — | Not imported anywhere outside tests |
| `src/models/poisson.py` | — | Not imported anywhere |
| `src/models/halftime.py` | — | Not imported anywhere |
| `src/models/injuries.py` | — | Not imported anywhere |
| `src/models/late_goals.py` | — | Not imported anywhere |
| `src/models/ml_ensemble.py` | — | Not imported anywhere |
| `src/models/ensemble.py` | — | Not imported in live pipeline |

`lifecycle.py`, `drift_detector.py`, and `retrain_worker.py` are imported by `src/monitoring/monitoring_coordinator.py` — they are active but only fire if monitoring coordinator is running.

### 4.4 🟡 `src/portfolio/self_optimizing_allocator.py`

214 lines. Defines `SelfOptimizingAllocator` and `get_self_optimizing_allocator()`. Zero imports from any other file. The similar `AdaptiveAllocator` in the same package is active.

### 4.5 🟢 `src/backtesting/` — CLI-only

Used exclusively by `src/cli/backtest.py` which has no automated callers. Legitimate offline tool, not dead code, but not integrated into the live pipeline.

### 4.6 🟢 `src/ingestion/backfill.py` vs `scripts/backfill_all.py`

Two separate `Backfiller` class implementations. `src/ingestion/backfill.py` is a cleaner library version; `scripts/backfill_all.py` is a larger orchestrator that also handles EuropeanBackfiller. `backfill_cron.py` wraps the scripts version. The library version (`src/ingestion/backfill.py`) is not directly called by anything active.

---

## 5. Flow Inconsistencies

### 5.1 🔴 `scripts/web_ui.py` — 8,106-line monolith

202 route functions in one file. The web UI directly imports from 40+ modules across `backend/`, `src/`, `config/`, and `scripts/`. It contains inline business logic (prediction fallback code, feature vector construction, model loading) that duplicates `src/betting/prediction.py` and `src/models/trainer.py`.

The web UI has its own h2h feature builder (corrected in a previous session after mismatch) instead of calling `build_features_h2h()` from `src/betting/prediction.py`. Any future change to the feature schema must be applied in both places.

**Specific duplicates inside web_ui.py:**
- Feature construction for predictions (lines ~3885–3898): duplicates `src/betting/prediction.py:build_features_h2h()`
- Calibration trigger code (line 6604): calls `backend.execution_engine._fit_calibrator_for_market` — a private function
- Round close logic (line 7705): duplicates `src/betting/round_manager.close_round_if_full()`

### 5.2 🟡 `backend/config.py` — documented shim but still imported

`backend/config.py` explicitly says "DO NOT add new settings here." However, it is imported by `backend/app.py`, `backend/scheduler.py`, and indirectly by anything that imports `backend/app.py`. The shim adds an import hop on every startup. The underlying `config/settings.py` should be imported directly in new code.

### 5.3 🟡 Two parallel `auto_bet` / `coordinator` paths

There are two paths through which bets can be placed:

**Path A (old):** `scripts/auto_bet.py` → direct DB writes, local Kelly sizing, no policy engine  
**Path B (new):** `src/agents/coordinator.py` → Markowitz → `src/governance/policy_engine.py` → `src/betting/execution_engine.py`

`backend/execution_engine.py` registers both — `_run_betting_pipeline()` calls `auto_bet.run_auto_bet_pipeline()` while the runtime also wires up the coordinator. Both paths may fire in the same cycle depending on the scheduler job configuration. It is unclear whether they are mutually exclusive at runtime.

### 5.4 🟡 `data/scheduler.py` — superseded APScheduler init

`/opt/projects/bootball/data/scheduler.py` contains the original APScheduler initialization code with job registration for daily jobs. It is not imported anywhere. `backend/scheduler.py` is the live replacement. Having this file in `data/` (normally a data directory) is misleading.

### 5.5 🟢 `src/ingestion/backfill.py` never called by runtime

`src/ingestion/backfill.py` contains a well-structured `Backfiller` class but the runtime calls `scripts/backfill_all.py` (via `backfill_cron.py`). The library version accumulates in `src/` without being used.

---

## 6. Configuration Audit

### 6.1 🟡 `config/pre_league.py` — 1,912 lines, role unclear

`config/pre_league.py` is 76,530 bytes (largest config file). It appears to contain historical league/team pre-population data. It is not imported anywhere in the codebase (`grep` finds zero refs). If it's seed data, it should be a SQL migration, not a Python module.

### 6.2 🟢 `config/markets.py` vs `src/betting/market_taxonomy.py`

`config/markets.py` defines a `MARKETS` dict. `src/betting/market_taxonomy.py` defines `MARKET_TAXONOMY` with the same four markets (h2h, btts, ou25, ou15) plus helper functions. `config/markets.py` is imported in a few places but carries less information than `market_taxonomy.py`. Minor overlap.

---

## 7. Test Coverage Gaps

| Area | Has Tests |
|---|---|
| `src/governance/policy_engine.py` | Yes — `tests/integration/test_policy_constraints.py` |
| `src/betting/portfolio/markowitz_optimizer.py` | Yes — `tests/integration/test_portfolio_optimizer.py` |
| `src/settlement.py` | No |
| `src/agents/coordinator.py` | No |
| `src/betting/execution_engine.py` | No |
| `scripts/auto_bet.py` | No |
| `src/betting/prediction.py` | No |

Settlement and execution engine have no automated tests despite being the highest-risk components.

---

## 8. Priority Action List

### 🔴 Do first (correctness risk)

1. **Remove `scripts/settle_bets.py`** — broken (`ValueBet` import fails), not called, actively misleading.
2. **Fix `src/alerts/handlers.py:191`** — imports `ValueBet` from `capital_allocator.py` as if it's a DB model; the import will crash in that handler path.
3. **Confirm which bet placement path is live** — `auto_bet.py` direct writes vs coordinator path. If both fire, stakes could be double-placed.

### 🟡 Do next (maintainability)

4. **Remove confirmed dead scripts** (§3.1): `daily_run_backup.py`, `retrain_models_new.py`, `train_multi_league.py`, `extensive_logging.py`, `live_stats_collector.py`, `setup_db.py`, `data/scheduler.py`, `src/ingestion/scheduler.py`.
5. **Consolidate portfolio path** — remove `src/portfolio/self_optimizing_allocator.py` and audit whether `src/betting/portfolio_optimizer.py` (the standalone one) is still needed, or if its correlation-avoidance logic is now covered by `markowitz_optimizer.py`.
6. **Move web_ui.py prediction fallback** — the inline feature builder should call `build_features_for_market()` from `src/betting/prediction.py`, not reimplement it.
7. **Rename `scripts/maintenance.py`** → `scripts/diagnostics.py` to avoid confusion with `src/maintenance.py`.

### 🟢 Backlog (cleanup)

8. Archive dormant model files (`dixon_coles.py`, `elo_predictor.py`, `poisson.py`, `halftime.py`, `injuries.py`, `late_goals.py`, `ml_ensemble.py`) into a `src/models/experimental/` subdirectory with a README.
9. Have `auto_bet.py` import `get_market_result` from `src/settlement.py` instead of maintaining `_get_market_result` privately.
10. Add tests for `src/settlement.py` and `src/betting/execution_engine.py`.
11. Investigate `config/pre_league.py` — if it is seed data, convert to a migration file.
12. Consolidate the two Discord posting paths (direct `wire_to_event_bus()` in `discord_system_notifier.py` and `discord_consumer.py` subscription) to prevent duplicate notifications on the same event.

---

## 9. Metrics Summary

| Metric | Count |
|---|---|
| Total project Python files (excl. venv) | ~220 |
| Files in live execution path | ~45 |
| Confirmed dead/orphaned scripts | 9 |
| Likely dead scripts (verify) | 7 |
| Duplicate logic sites | 6 |
| Portfolio optimizer implementations | 6 (3 active, 1 semi, 1 orphaned, 1 fallback) |
| Copies of `get_market_result()` | 3 |
| Lines in web_ui.py | 8,106 |
| Models defined but unused in live pipeline | 8 |
| Settlement tested by automated tests | No |
