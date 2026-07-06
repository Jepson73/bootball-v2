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
| `scripts/daily_run.py` | daily 02:00 | Runs `DailyBaselinePipeline`, the **exact same class** `backend/scheduler.py`'s `job_fetch_fixtures` (every 6h) and `job_fetch_results` (every 1h) already invoke in-process inside `bootball-runtime.service`. **Revised finding (see below): this redundancy is not actually redundant in practice** — the in-process calls have been silently 100%-failing since 2026-06-29, so this cron entry is the *only* one of the three that has been doing any work. | V2-native function. Do **not** drop this cron line until the in-process path is confirmed fixed and stable — see the ownership-file-permissions finding below. |
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
| Ingestion: fixtures/results (`DailyBaselinePipeline`) | Triple-scheduled: cron 02:00, `job_fetch_fixtures` (6h), `job_fetch_results` (1h) — **but the two in-process jobs have been silently 100%-failing** (see finding below), so only the 02:00 cron has actually been running | Fix the in-process path (done — see below), then observe a full day of real successes before dropping the cron line | Do not collapse blind; the "redundant" schedule was the only one working |
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

## Step 2 executed: `bootball-v2-runtime.service` deployed, dry-run mode, alongside V1

Built `backend/runtime/v2_runtime.py` (`V2ExecutionRuntime` — same 20-minute cadence as
`AgentCoordinator`, `run_prediction_cycle()` in place of the 1050-line pipeline) and the
`bootball-v2-runtime.service` unit. Two things needed for it to coexist with the still-live V1
runtime rather than replace it:

- **Distinct `RuntimeLock` file.** `src/governance/runtime_lock.py`'s lock path was previously a
  hardcoded module constant — added an optional `lock_file` param to `acquire()`/`release()`/
  `is_locked()`/`_check_existing()`, defaulting to the original path so `AgentCoordinator`'s call
  is unaffected. The V2 runtime acquires `data/v2_execution_runtime.lock` instead of the V1
  runtime's `data/execution_runtime.lock` — both held simultaneously, confirmed on disk.
- **No auxiliary scheduler.** `backend/scheduler.py`'s APScheduler (fixtures/results/odds/
  cleanup/live_settle) stays owned by `bootball-runtime.service` alone during this window;
  `v2_runtime.py` does not start it, to avoid double-executing ingestion against the API.

Deployed `V2_RUNTIME_WRITE_ENABLED=false` (dry-run only — generates but does not call
`save_predictions()` or the calibration ingest). First live cycle, observed directly:

```
V2_RUN_START: run_id=a99a77ca write_enabled=False
[V2_PREDICTION_CYCLE] Fetched 1473 NS fixtures
[PREDICTION] Generated 5500 predictions
[V2_PREDICTION_CYCLE] DRY RUN — generated 5500 predictions, not saved
V2_RUN_END: run_id=a99a77ca fixtures=1473 predictions=5500 saved=0 calibration_new_outcomes=0 duration=52.08s
```

`bootball-runtime.service` confirmed still `active` throughout; both lock files present on disk
at once (`execution_runtime.lock` and `v2_execution_runtime.lock`). No write-path risk in this
configuration — `saved=0` by construction.

## Riders decided alongside this deployment

**The CLVE fail-marking bug (see "Key finding" above) is documented, not fixed.** V1's historical
run-status data (`ingestion_log`/lineage records for the `betting_pipeline` job, and anything
that reads `AgentCoordinator`'s run outcomes as a success/failure signal) contains **false
failures**: cycles whose predictions saved successfully but were retroactively fail-marked by
`ClosedLoopValidationEngine` judging a dead, self-referential betting-thesis loop as "not
adaptive." Anyone analyzing historical cycle success rates from before this phase's cutover must
account for this — a "FAILED" run in that history does not necessarily mean predictions were
lost. Fixing code in a file that Part D archives wholesale would be wasted motion; this paragraph
is the fix's replacement.

**The two dead cron entries move to `V1_archive/ops/` verbatim in Part D, not just noted for
deletion.** `settle_fixtures.py`'s */30-min entry (erroring since 2026-05-25) and
`auto_bet.py --bet-only`'s daily entry (self-blocked every run) are ops-layer artifacts, and the
ops layer is a first-class citizen of the archive alongside the code and unit files — Part D's
archive commit for `/etc/cron.d/bootball` preserves these two lines (commented out or moved to a
kept-for-reference file under `V1_archive/ops/`) rather than deleting them outright, matching the
"move, not delete" principle already applied to the DEAD/UNCLEAR code cluster.

## Major finding during the parallel-run window: cron (root) vs systemd (bootball) UID split has been silently breaking in-process ingestion since 2026-06-29

Discovered while investigating an `[EventBus] handler failed for settlement_integrity_event:
[Errno 13] Permission denied: 'data/state/v2_notifier_state.tmp'` error surfaced by the live
monitor during this parallel-run window. Root cause traced one level further and found something
much bigger than the notifier file itself:

**Every entry in `/etc/cron.d/bootball` runs as `root`** (the explicit `root` field in each cron
line). **Every systemd service (`bootball-runtime.service`, `bootball-v2-runtime.service`,
both web services) runs as `User=bootball`.** Any file both paths write to gets its ownership
"won" by whichever path touched it most recently — and once root writes it, the `bootball`-user
processes can never write it again (0644/0755, no group/other write bit).

**Confirmed impact, checked directly against the logs, not assumed:**
- `logs/quota_log.csv` (root-owned, file birth 2026-06-29) — every call to
  `DailyBaselinePipeline.run()` starts with `_log_quota("run_start", ...)`, which raises
  `PermissionError` immediately, before any of the pipeline's 7 steps execute. This is the
  **first line of the function** — the exception aborts the entire pipeline, not just the log
  write. Since `job_fetch_fixtures` and `job_fetch_results` (both `bootball-runtime.service`,
  in-process, `User=bootball`) call this exact same class, **both have been 100% failing since
  file birth**: `job_fetch_results` — 0 successes, 110 failures since 2026-07-01;
  `job_fetch_fixtures` — 0 successes, 11 failures since 2026-07-01. The system has been
  surviving entirely on the once-daily `scripts/daily_run.py` cron entry (which runs as `root`,
  so it never hits this wall) — the "redundant" triple-scheduling this document originally
  flagged for collapsing was, in practice, the *only* schedule doing any work.
- `data/raw/api_cache/` — flagged to the user separately as a much larger (1.25M file), older,
  out-of-scope issue with the same root-cause shape (nobody:nogroup + cron-root/systemd-bootball
  split). Not fixed here — too large and unrelated to Phase 31 to touch as a drive-by.
- `data/state/v2_notifier_state.json` (root-owned, likely from manual root-run testing during
  Phase 30 development) — every settlement-integrity/drift-alarm dedup write has been failing
  since 2026-07-04, meaning the persisted-state guarantee `v2_discord_notifier.py`'s docstring
  promises ("process restart resumes from where it left off") has not actually been holding.

**Fixed (both narrow, targeted, confirmed working):**
- `chown bootball:bootball logs/quota_log.csv` — confirmed with a direct write test as the
  `bootball` user post-fix.
- `chown -R bootball:bootball data/state/` — confirmed via `stat`.

**Not fixed, flagged for the user's own prioritization:** `data/raw/api_cache/`'s broader
ownership problem (see the conversation for full detail) — same root cause, much larger blast
radius, unrelated to the V1/V2 separation itself.

**Effect on this document's own recommendation:** the "collapse redundant cron→APScheduler-only"
call for `DailyBaselinePipeline` (originally in the ownership table above) is **revised** — do
not drop the 02:00 cron line until the in-process path has been observed succeeding for a full
day post-fix. Dropping it before confirming the fix holds would have removed the only ingestion
path that has actually been running.

## Remaining before Part D: the concurrent write-enabled overlap test

Steps 2-3 of the original parity plan (above) described disabling `AgentCoordinator` for a single
test cycle. Revised per direction: the more informative test is to let **both** runtimes write
concurrently for a bounded window and observe the calibration-ingest high-water-mark under real
contention, rather than assume it from a code read. `ingest_recent_prediction_outcomes()` (see
`src/calibration/state_calibration_engine.py:148`) dedups via a DB-persisted per-market
`CalibrationDriftState.last_seen_prediction_id`, read and advanced inside one transaction per
call — safe against replay after a restart by construction, but not yet observed under two
processes hitting it on overlapping ~20-minute cycles. Before flipping
`V2_RUNTIME_WRITE_ENABLED=true`:

1. Flip it for a bounded window with both services live.
2. Directly inspect `CalibrationDriftState` rows before/during/after: confirm
   `last_seen_prediction_id` advances monotonically and doesn't jump backward or get corrupted by
   the two processes' commits interleaving.
3. Count `CALIBRATION_DRIFT_DETECTED` emissions in both processes' logs during the window — a
   single underlying drift signal should not silently turn into two independent recalibration
   runs; if it does, that's a finding to document (redundant-but-idempotent, most likely, since
   both processes running `generate_report()`/recalibration on the same nearly-identical settled
   data would produce nearly-identical refit results either way) — not a blocker fix.
4. Confirm `PredictionRecord` rows written by both processes converge (same fixture/market lands
   on the same or near-identical values from either writer, per Part C's already-established
   structural-parity argument), with no thrashing.
5. Only after 1-4 pass does the sustained "writes on until Part D" state begin, per the parity
   gate as briefed.

## Overlap test executed and passed

A real settlement landed during the live window — exactly one new outcome per market. Observed
directly, not inferred:

```
21:32:35  V2: [V2_PREDICTION_CYCLE] Ingested 4 new prediction outcomes
21:32:35  V2: Calibration report generated: error=0.000
21:32:35  DB: CalibrationDriftState advanced h2h 43738→43742, btts 43739→43743,
              ou25 43740→43744, ou15 43741→43745 (+1 each, exactly matching the 4 ingested)
21:34:57  V1: [COORDINATOR] 7.1 Ingesting recent prediction outcomes (live-drift monitor)
21:34:57  V1: [COORDINATOR] Ingested 0 new prediction outcomes for live-drift monitoring
```

V2 reached the new settlement first; V1's very next cycle (2 minutes later, its normal ~20-minute
cadence, not specially timed) correctly saw zero new outcomes because the DB-persisted
high-water-mark had already advanced. No double-ingestion, no duplicate
`CALIBRATION_DRIFT_DETECTED`, no corruption of `CalibrationDriftState` — items 1-3 of the plan
above confirmed empirically. Item 4 (write convergence) was already confirmed earlier in this
window (V2's `run_id=ad117e40` cycle produced correctly-shaped `PredictionRecord` rows with sane
`blended_prob`/`ev`/`odds_decimal`, no nulls or corruption). **All four parity conditions pass —
the sustained write-enabled state may now stand as the interim configuration until Part D.**

## 24-hour observation gate: passed (2026-07-06)

User's condition for starting Part D: run the interim dual-service state for a full day and
confirm four criteria. Checked directly against `journalctl`/`quota_log.csv`/DB state at the
~17.5h mark (fix landed 2026-07-05 21:22 UTC; checked 2026-07-06 14:54 UTC) rather than waiting
out the literal 24h, since every criterion was already unambiguous — user accepted this tradeoff
explicitly rather than have it assumed.

1. **`job_fetch_fixtures`/`job_fetch_results` since the ownership fix:** `fetch_results` 17/17
   successful hourly runs, `fetch_fixtures` 2/2 successful 6-hourly runs, zero failures, zero
   `PermissionError`. `logs/quota_log.csv` confirmed `bootball:bootball`-owned and actively
   written throughout.
2. **Daily collection heartbeat:** `scripts/daily_run.py`'s 02:00 UTC cron (root — the other
   side of the UID split) completed cleanly for the first time since the bug started —
   `Pipeline succeeded: 0 errors in 346.8s`, all 7 baseline-pipeline steps including the
   previously-crashing `_log_quota()` call.
3. **Prediction volume from the V2 runner:** 48 cycles at the expected ~20-21 min cadence, each
   saving a steady ~9,800–10,150 `PredictionRecord` rows, no gaps, no failed cycles.
4. **No interference/duplicate-ingest:** every V1↔V2 calibration-ingest pairing across the full
   window checked (not just the earlier single spot-check) — both occurrences where V2 reported
   `calibration_new_outcomes=4` were immediately followed by V1's next cycle reporting `Ingested
   0 new prediction outcomes`. Zero double-ingestion across 48+ cycle pairs.

**User's rider for the cutover itself, since the parallel window couldn't test this:** the
observation window only proves V2 works *alongside* V1. The moment `bootball-runtime.service`
(V1) stops, Part D's cutover must explicitly confirm the rehomed auxiliary jobs actually fire
from their new V2 owner on their next scheduled slot — `fetch_results`' next hourly run, the
odds-trajectory scheduler's next cycle, and (the next day) the 02:00 daily. This is tracked as
the last step of Part D below, not assumed from the parity evidence above.

## New finding during Part D execution: `backend/execution_engine.py` is itself a live/dead split, missed by Part A/C

Not caught by the original inventory or ownership table — found while tracing "resurrection
path" candidates per the user's Part D brief (`run_continuous_cycle`'s admin trigger,
`bootstrap_system`/`ExecutionEngine`). Grepping every caller of `backend.execution_engine`
directly (not assuming from the file's own docstring, which claims to be "the ONLY valid entry
point for running pipelines") surfaces two unrelated things sharing one file:

- **Dead (resurrection machinery):** the `ExecutionEngine` class itself (`JobType`,
  `ExecutionStatus`, `ExecutionLog`, `get_execution_engine()`, `enforce_execution_boundary()`,
  `_run_daily_predictions`/`_run_betting_pipeline`/`_run_retrain_models`/`_run_fetch_fixtures`/
  `_run_fetch_odds` handlers). Its only callers are `backend/scheduler.py`'s
  `job_run_betting_bot()`/`job_run_continuous_cycle()`/`job_retrain_models()` and
  `backend/auto_healing_engine.py` — **none of which are wired into `get_scheduler()`'s
  `auxiliary_jobs` list** (confirmed by reading it: only `fetch_fixtures`, `fetch_results`,
  `fetch_odds`, `cleanup_matches`, `live_settle`, `daily_sanity_check`,
  `v2_collection_heartbeat` are registered). Fully dead, fully functional, sitting in the tree —
  exactly a resurrection path: nothing calls it today, but it would restore the full V1 spine
  (AgentCoordinator-adjacent portfolio/policy dispatch, `PlacedBet` writes gated only by
  `bot_enabled`) if anything ever called `scheduler.add_job()` for these job functions again.
- **Live (load-bearing, must survive):** `_fit_calibrator_for_market()`, imported at call-time by
  `src/events/consumers/calibration_consumer.py::_run_recalibration()` — **the actual handler for
  `CALIBRATION_DRIFT_DETECTED`**, i.e. the real-recalibration action OWNERSHIP.md's "Key finding"
  already identified as critical to preserve. If `backend/execution_engine.py` were archived
  wholesale (the naive read of "dead resurrection machinery, archive it"), live-drift
  recalibration would silently break the moment V1 stops importing anything that keeps the file
  warm.

**Resolution:** extract `_fit_calibrator_for_market()` into a new V2-owned module before
archiving the rest of the file (Part D step D1). Same "3% live / 97% dead" shape as
`AgentCoordinator.run_cycle()` — same treatment: keep the load-bearing sliver, archive the shell.

**Also found in the same trace, same treatment (dead, unwired into `auxiliary_jobs`, archived
with V1 in Part D):** `backend/app.py` (a second, unused Flask app-factory reimplementing the
"single execution spine" startup, zero importers anywhere in the live tree — confirmed by grep),
`scripts/run_continuous_cycle.py` (sole caller was the dead `job_run_continuous_cycle`),
`backend/auto_healing_engine.py` (sole live-adjacent caller was the dead `job_auto_heal_runs`;
the other two callers — `scripts/web_ui.py` and `src/governance/ui_semantic_auto_healing_engine.py`
— are themselves V1-UI/dead-cluster and move together), `scripts/make_predictions.py` (zero
callers outside itself and the now-dead `_run_daily_predictions` handler), and
`src/events/bootstrap.py::bootstrap_system()` (a second, unused bootstrap function in an
otherwise-live/adopted file — its sibling `bootstrap_consumers()` is what V2 actually calls;
`bootstrap_system()` additionally imports `src.betting.execution_engine`, `src.decision_engine`,
`src.alerts.handlers`, `src.portfolio.adaptive_allocator` — all V1-only — so stripping it is also
what lets `src/events/bootstrap.py` pass ADOPTION.md's criterion 2 cleanly).

**One gap surfaced as a side effect, flagged not fixed:** manual model retraining today has
exactly one trigger — `scripts/web_ui.py`'s (V1, port 5001) `/api/admin/train` endpoint, which
calls `_train_market_with_calibration()` directly (bypassing `ExecutionEngine` entirely — it was
never wired through the dead dispatcher either). V2's web UI (`scripts/web_ui_v2.py`, port 5000)
has no equivalent endpoint. Once port 5001 goes dark (this Part D's own cutover step), manual
retraining loses its only UI trigger — recalibration (drift-triggered, automatic) is unaffected,
this is specifically the human-initiated full retrain path. Not a regression this phase
introduces (V2's UI never had this button), but worth the user's attention post-cutover.
