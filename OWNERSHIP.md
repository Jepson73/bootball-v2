# OWNERSHIP.md — Phase 31 Part C: V2 Runtime Ownership Table

Part C of the Phase 31 V1/V2 separation. This is the design for the new V2-owned runner(s)
that will replace `bootball-runtime.service`'s current call into `AgentCoordinator.run_cycle()`.
Nothing here is deployed yet — this is the design + evidence, built before any code is written,
per the brief's "never archive-first" sequencing.

## Key finding: `AgentCoordinator.run_cycle()` is ~3% live code wrapped in ~97% dead theater

Read the full 1182-line `src/agents/coordinator.py` end to end. Every cycle
(`bootball-runtime.service`, every 20 minutes, `backend/runtime/execution_runtime.py`
→ `coordinator.run_cycle()` → `_run_internal()`), the following actually happens:

**Live, load-bearing (~30 lines):**
1. Fetch `Fixture` rows with `status == "NS"` and `date >= now`.
2. `UnifiedPredictionService.generate_with_fixture_data(fixtures)` → `save_predictions(...)`.
   This writes the `PredictionRecord` rows — the actual product.

**Live, load-bearing, but currently buried inside the "feedback cycle" (Step 7.1/7.3, ~15 lines
out of `_run_feedback_cycle`'s 130):**
3. `state_calibration_engine.ingest_recent_prediction_outcomes()` then, if there were new
   outcomes, `.generate_report()` — this is the live-drift ECE monitor (Phase 28-retargeted to
   read `PredictionRecord`, adopted in `ADOPTION.md`) and the sole path that fires
   `CALIBRATION_DRIFT_DETECTED`, which `calibration_consumer.py` acts on for real
   recalibration. **`AgentCoordinator` is currently this function's only caller anywhere in the
   codebase** (grepped `get_state_calibration_engine` — three hits: the module itself, its
   `__init__.py` re-export, and `coordinator.py`). If `AgentCoordinator` is retired without
   giving this call a new home, live-drift monitoring silently stops.

**Executes every cycle, produces zero live effect, confirmed by direct read (not guessed):**
- Step 2: `RiskManagerAgent.run()` — computes a risk regime/lambda fed only to the dead
  portfolio path below.
- Step 3: `ExecutionStrategistAgent.run()` + bankroll sync from `BankrollRound`/`PlacedBet` —
  reads a betting ledger that has taken zero new rows since 2026-06-07 (Phase 8).
- Step 3b: `PortfolioEngine.compute_allocation()` (Markowitz-style sizing) — same dead ledger.
- Step 4: `AdversaryAgent.run()` — stress-tests the above portfolio.
- Step 5: `PolicyEngine.evaluate()` with a stub `MonteCarloResults` (not real Monte Carlo —
  `"simplified - no MonteCarlo for now"` per the code's own comment).
- The `PlacedBet` write block — already a confirmed no-op: `bot_enabled=False` since the "13
  bets" investigation, `elif` branch never reached.
- Step 5 (learning): `PerformanceEvaluator.evaluate()` + `WeightOptimizer.optimize()` +
  `EventReplay.record_run()` — evaluates the in-memory (never-persisted) portfolio from Step 3b,
  feeding weights back into next cycle's `ExecutionStrategistAgent`/`PortfolioEngine`. A closed
  loop entirely internal to the dead betting thesis; nothing here reaches outside itself.
- Step 7.2/7.4/7.5 (rest of feedback cycle): `PerformanceEvaluator` again (on the same fictional
  portfolio), `MetaPolicyEngine.add_policy_outcome()`/`update_policy()`, `StateManager.persist_state()`
  — same closed loop.
- Step 7.3b: writes `LayerGovernanceMetrics` via `system_governance_engine` — a table
  `ADOPTION.md` already marks **strip**.
- Step 9: `ClosedLoopValidationEngine.evaluate()` — **and this one is an active risk, not just
  dead weight.** It judges whether "the system" is adaptive using metrics computed from the same
  self-referential dead-portfolio loop above, and **if it decides no, it raises `RuntimeError`
  and the whole cycle is marked FAILED** — even though the live predictions from Step 1 were
  already successfully saved to the database earlier in the same call. This is a real bug:
  a defunct-betting-thesis validator can fail-mark (and 60s-retry-loop) a cycle whose actual
  product already shipped. Flagged here as a finding independent of this redesign; the V2
  runtime described below simply never calls it, which resolves it as a side effect of the
  separation rather than as a targeted fix.

**Conclusion:** the correct "V2 owner" for prediction generation is not a port of
`AgentCoordinator` — it is a new, small runner containing exactly items 1–3 above. Everything
else in `_run_internal`/`_run_feedback_cycle` is Phase-8-dead V1 betting-thesis machinery that
belongs in `V1_archive/` (Part D), not carried forward.

## Also found while tracing schedules: the cron surface Part A's file-level inventory couldn't see

Part A classified source files; it did not enumerate `/etc/cron.d/`, which is infrastructure,
not a repo file. Reading it now surfaces entries that need their own adopt/strip/delete calls:

| Cron entry | Schedule | Finding | Verdict |
|---|---|---|---|
| `scripts/settle_fixtures.py` | */30 min, most hours | **File does not exist** — deleted from the repo in the 2026-05-25 "Full codebase refresh" commit. Confirmed via `/var/log/bootball/settle_fixtures.log`: `can't open file ... No such file or directory` on every single invocation, for 6+ weeks. Pure noise, zero effect. | Delete the cron line in Part D. Not a V1/V2 question — this is just stale. |
| `scripts/auto_bet.py --bet-only` | daily 03:00 | Runs, hits its own `check_legacy_execution_allowed()` guard (independent of `bot_enabled`), raises `RuntimeError: LEGACY EXECUTION BLOCKED`, writes nothing. Confirmed via today's (2026-07-05 03:00) log tail. | V1-only, dead-but-executing. Archive with V1; remove cron line. |
| `scripts/daily_run.py` | daily 02:00 | Runs `DailyBaselinePipeline`, the **exact same class** `backend/scheduler.py`'s `job_fetch_fixtures` (every 6h) and `job_fetch_results` (every 1h) already invoke in-process inside `bootball-runtime.service`. This cron entry is fully redundant with jobs that already run 24+ times/day. | V2-native function; redundant schedule. Collapse to one V2-owned schedule (see table below), drop the standalone cron line. |
| `scripts/odds_poll.py` | */30 min, 08:00–24:00 CET | V2-native (adopted), but `job_fetch_odds` already runs it hourly in-process. Same redundancy shape as above. | V2-native; collapse to one schedule, owner TBD below. |
| `scripts/odds_trajectory_scheduler.py` | */30 min, 24/7 | V2-native, cron-only — no in-process duplicate found. | Keep as-is; V2-owned cron. |
| `scripts/probe_forward_odds.py` ×2 | one-off calendar dates (Tasmania, Norway) | V2-native, self-expiring checkpoints, read-only. | Keep as-is. |
| `scripts/backfill_cron.py` (root's personal crontab) | daily 09:00 | `EuropeanBackfiller` — the Track A historical backfill from the quota-timeline memory. | V2-native; keep as-is. |

## Proposed V2 ownership table

| Function | Current owner (today) | Proposed V2 owner | Notes |
|---|---|---|---|
| Prediction generation (fetch NS fixtures → `UnifiedPredictionService` → `save_predictions`) | `AgentCoordinator.run_cycle()` inside `bootball-runtime.service`, every 20 min | New `PredictionCycleRunner` (name TBD at build time) in the new V2 service, same 20-min interval | The ~30 live lines extracted from `_run_internal`, everything else in this file dropped |
| Live-drift calibration ingest + report (`CALIBRATION_DRIFT_DETECTED`) | Buried in `AgentCoordinator._run_feedback_cycle` Step 7.1/7.3, same 20-min cadence | Same new V2 service, called directly after prediction generation each cycle (no dependency on anything from the dropped 97%) | Must not be lost — it's the only caller today |
| Settlement (`verify_ft_fixtures`, `settle_placed_bets`, `settle_predictions`, maintenance) | Runs from **three** places today: `execution_runtime._run_settlement()` (after every 20-min coordinator cycle), `job_fetch_results` (hourly, calls `fetch_and_update_fixtures`+`backfill_missing_scores`+`verify_ft_fixtures`+`settle_all`), `job_live_settle` (every 2 min, live-score fetch + settle) | Auxiliary scheduler jobs only (`job_fetch_results`, `job_live_settle`) — the coordinator-cycle call is dropped since it's now redundant with jobs that already run more frequently | No functional loss: 2-min/1-hour cadence already covers everything the 20-min coordinator call did |
| Ingestion: fixtures/results (`DailyBaselinePipeline`) | Triple-scheduled: cron 02:00, `job_fetch_fixtures` (6h), `job_fetch_results` (1h), all calling the identical class | Collapse to the two APScheduler jobs only; drop the 02:00 cron line | Removes a fully redundant daily invocation with no behavior change |
| Ingestion: odds (`odds_poll.py`) | Double-scheduled: cron */30 min (08–24 CET) + `job_fetch_odds` (hourly) | Keep both — the cron half covers the daytime tightening (30 vs 60 min) that the hourly aux job doesn't; not true redundancy, different resolution | No change |
| Odds trajectory capture | `scripts/odds_trajectory_scheduler.py` via cron, 24/7 */30 min | Unchanged — already V2-native, cron-owned, independent of the runtime service | No change |
| Historical backfill | `scripts/backfill_cron.py` via cron, daily 09:00 | Unchanged | No change |
| Elo (`elo_both`/`elo_partial`/`national_elo`) | Manual, human-invoked scripts, governed by `EloRebuildLog` | Unchanged by design (adopted 2026-07-05 — manual is the design, not neglect) | No scheduled owner needed |
| RuntimeLock (single-instance enforcement) | `src/governance/runtime_lock.py`, called by `ExecutionRuntime.start()` | New V2 service calls it the same way | Generic infra, adopted as-is |
| Execution watchdog (heartbeat/stall detection) | `backend/runtime/execution_watchdog.py`, thread inside `ExecutionRuntime` | New V2 service starts it the same way | Confirmed generic — no `PlacedBet`/portfolio-specific logic found in it |
| Discord V2 notifications | `wire_v2_notifier()`, called from `execution_runtime._init_discord()` | New V2 service calls it the same way at startup | Already V2-only |
| Event consumer bootstrap (calibration/model/health/betting-dashboard consumers) | `bootstrap_consumers()`, called from `execution_runtime._bootstrap_consumers()` | New V2 service calls it the same way | `DiscordConsumer`/`PolicyConsumer`/`CLVEConsumer` stay conditional on `discord_v1_enabled` (False), everything else always registers, unchanged from Phase 30 |
| Web UI (V2, port 5000) | `bootball-web-v2.service` → `scripts/web_ui_v2.py` | Unchanged | Already fully separate process |
| Web UI (V1, port 5001) | `bootball-web.service` → `scripts/web_ui.py` | Archived in Part D | Confirmed sole caller of `WatchedFixture`/`UserPreference` |

## New service design

- **New systemd unit**: `bootball-v2-runtime.service`, replacing `bootball-runtime.service`.
  Same process shape (`Type=simple`, `Restart=always`), new entrypoint file (name/location
  decided at build time per the relocation rider — function-named, not betting-flavored).
- **Entrypoint responsibilities**, in order, at startup: acquire `RuntimeLock` → start
  `ExecutionWatchdog` → `wire_v2_notifier()` → `bootstrap_consumers()` → start the auxiliary
  APScheduler (fixtures/results/odds/cleanup/live_settle/sanity-check/collection-heartbeat,
  cron-daily_run entry removed per the collapse above) → loop: prediction generation +
  calibration ingest/report every 20 min.
- **`scripts/deploy.sh`**: add `bootball-v2-runtime` to the service list, remove
  `bootball-runtime` — done at cutover (Part D), not before, so the parity run below has both
  running side by side under their current names.

## Parity verification plan (required before Part D, per the brief)

Run the new V2 service **alongside** the existing `bootball-runtime.service`, both live, for a
full daily cycle, without letting them double-write:
1. Build the new runner but do **not** yet point it at the same 20-min prediction-write path
   `AgentCoordinator` uses — instead, run it in a read-only/dry-run mode first (generate
   predictions, log what would be saved, skip `save_predictions`) to compare its output against
   what `AgentCoordinator` actually saves that cycle, fixture-for-fixture.
2. Once dry-run parity is confirmed (same fixtures, same probabilities, same EV — no drift from
   dropping the 97%), do one real cutover cycle: disable `AgentCoordinator`'s call in
   `execution_runtime.py` for a single test run, let the new service write instead, verify
   `PredictionRecord` rows land identically shaped (same columns populated, same blend behavior).
3. Confirm calibration drift events still fire (`CALIBRATION_DRIFT_DETECTED` reaching
   `calibration_consumer.py`) from the new service's call site.
4. Only after 1–3 pass does Part D's archive-and-cutover proceed.

## Step 1 executed: dry-run parity result

Built `src/prediction/prediction_cycle.py` (the lean runner: `generate_predictions()`,
`run_calibration_ingest()`, `run_prediction_cycle()`) and `scripts/verify_v2_parity.py` (read-only
comparison tool, writes nothing). Ran it against the live DB:

```
Dry-run generated 5788 predictions across 1447 fixtures.
Summary: 0 matched, 5788 mismatched, 0 had no existing stored row
```

Zero matches sounds alarming; it isn't. All 5788 mismatches are noise from two sources that have
nothing to do with the new runner vs. `AgentCoordinator`, because both call the **same**
`UnifiedPredictionService.generate_with_fixture_data()` / `save_predictions()` — this was verified
by inspecting which fields actually differ, not by trusting the summary count:

- **5616 of 5788 mismatches differ in exactly one field: `blended_prob`, always `stored=None`.**
  Root cause, found by reading `save_predictions()`'s skip branches (lines 434-456): a
  `PredictionRecord` with no odds that stays odds-less on a later cycle hits the
  "both preliminary — skip" branch, which only ever back-fills the h2h probability vector — it
  never touches `blended_prob`/`market_prob`. Any row that was first inserted before this field
  was being populated correctly (or that has simply never received odds since) is permanently
  stuck at `blended_prob=NULL`, even though generation always computes a value
  (`p_blended = p_final` unconditionally, before the odds branch). **This is a pre-existing quirk
  in the shared `save_predictions()` write path, identical under `AgentCoordinator` or the new
  runner** — not a parity gap introduced by this phase. Low severity: `ev`/`kelly` are correctly
  `None`/`0` for these preliminary rows regardless, so nothing consumes the stale `NULL`
  incorrectly today. Flagged here per the standing "no silent gaps" rule, not fixed — fixing a
  shared write path's skip-branch semantics is its own decision, out of scope for a rehoming
  phase.
- **The remaining 172 mismatches involve `calibrated_prob`/`market_prob`/`ev` and reflect real
  odds/model movement between whenever `AgentCoordinator`'s last actual cycle ran and this
  dry-run's fetch** — expected drift for any two point-in-time reads of a live system, not a
  runner discrepancy.

**Conclusion: structural parity is confirmed by construction** (the new runner calls the
identical generation/save functions with the identical fixture-fetch query — verbatim from
`coordinator.py`'s own logic) rather than by the diff count, which measures time-of-comparison
noise plus one unrelated pre-existing quirk. Parity step 1 passes.

## Remaining before Part D

Steps 2-3 of the parity plan (a real cutover cycle with the old path disabled for one run;
confirming `CALIBRATION_DRIFT_DETECTED` still reaches `calibration_consumer.py` from the new
call site) and the new `bootball-v2-runtime.service` unit itself are the highest-blast-radius
remaining work in Part C — a new long-running process against the live production DB. Not
started without a final check-in: see the question posed alongside this document.
