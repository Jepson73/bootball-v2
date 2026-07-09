# AUDIT_V2_STANDALONE.md — Phase 31 Part E: Standalone Re-Audit of V2

Part E of the Phase 31 V1/V2 separation. Part D's D0–D10 archived and cut over incrementally,
each step building on the previous step's reachability list. This audit does not trust that
accumulated narrative. It recomputes the live import/reachability graph from scratch — seeded
only from the actual live entry points, not from the D-phase task list — and cross-checks it
against every V1-flavored identifier and directory. Two items were called out explicitly for
this pass beyond that general checklist: the `odds_poll.py` → `alerts.py`/`kelly.py` import had
to be resolved as strip-or-justify, not left ambiguous; and the resurrection paths flagged
throughout Part D (`run_continuous_cycle`'s admin trigger, `bootstrap_system()`,
`ExecutionEngine`) had to be independently reverified as dismantled, not merely relocated.

Date: 2026-07-09.

## Method

1. Enumerate the actual live entry points directly from the host — `systemctl`,
   `crontab -l` — not from any doc's prior claim about what they are.
2. For each entry point, trace its import graph by hand (`grep`, direct `python -c "import ..."`)
   to build the full set of live-reachable `.py` files.
3. Sweep `src/`, `backend/`, `scripts/` for every V1-flavored identifier
   (`AgentCoordinator`, `ExecutionRuntime`, `ExecutionEngine`, `run_continuous_cycle`,
   `bootstrap_system`, `src.agents`, `src.governance`, `src.portfolio`) and classify every hit as
   either a live call/import (finding) or a comment/docstring reference to now-archived history
   (noise).
4. Confirm directory-emptiness for every path D-phase claimed to have fully evacuated.
5. Confirm `pytest --collect-only` still succeeds (no import-time breakage from any move).

## Live entry points (verified directly against the host, 2026-07-09)

| Entry point | Mechanism | Target |
|---|---|---|
| `bootball-v2-runtime.service` | systemd, enabled, in `multi-user.target.wants/` | `backend/runtime/v2_runtime.py` |
| `bootball-web-v2.service` | systemd, enabled, in `multi-user.target.wants/` | `scripts/web_ui_v2.py` (gunicorn, port 5000) |
| `backfill_cron.py` | root crontab, `0 9 * * *` | `scripts/backfill_cron.py` |
| `job_fetch_fixtures`, `job_live_settle`, `job_fetch_results`, `job_fetch_odds`,
  `job_cleanup_matches`, `job_daily_sanity_check`, `job_v2_collection_heartbeat` | in-process APScheduler jobs registered by `backend/scheduler.py`, itself imported and started by `v2_runtime.py` (V2 owns the scheduler since D9) | pull in `scripts/daily_run.py`, `scripts/odds_poll.py`, `scripts/daily_sanity_check.py`, and `src/settlement.py` (`update_pending_fixture_scores`, `settle_placed_bets`, `fetch_and_update_fixtures`, `settle_all`, `backfill_missing_scores`, `verify_ft_fixtures`) |

`crontab -l` was re-checked directly this pass and contains exactly one line
(`backfill_cron.py`, 09:00 daily) — confirming D8's removal of the two dead V1 cron lines held
and no new ones have appeared. `multi-user.target.wants/` contains exactly the two V2 units
listed above (plus, as of this pass, no longer `bootball.service` — see below).

## Item 1: `odds_poll.py` → `alerts.py`/`kelly.py` — resolved, not left ambiguous

D7c had left this an open finding: `alerts.py`/`kelly.py` stayed in `src/betting/` because
`odds_poll.py` — a genuinely live file, wired into `backend/scheduler.py`'s odds-refresh job —
imported `alerts.py` at module scope (which transitively pulled in `kelly.py` via
`src/betting/__init__.py`). Per the standing rule that nothing stays live by import-inertia, both
call sites were traced to their actual runtime effect rather than accepted as fact:

- `odds_poll.py`'s `main()` built `BetAlert` objects and called
  `BettingAlerts(channels=["discord"]).send_bet_alert(bet)` on every ~30-minute cron tick when
  odds changed — this executed in full, correctly, with no errors.
- `src/settlement.py`'s `send_settlement_alert()` did the same via `.send_message(msg)`, gated on
  `bet_details` being non-empty — which requires a newly-settled `PlacedBet` row.

Both paths bottom out in `alerts.py`'s `DiscordChannel.send()`, which gates on
`settings.discord_v1_enabled` — confirmed `False` by default and in the live environment
(silenced at Phase 30). `odds_poll.py`'s path is real execution producing zero observable effect.
`settlement.py`'s path is additionally unreachable at the data layer: a direct query
(`SELECT COUNT(*), MAX(placed_at) FROM placed_bets`) returned 448 total rows with
`MAX(placed_at) = 2026-06-07` — no new `PlacedBet` row has been created since, and none can be
while `bot_enabled=False`, so the `bet_details` precondition can never fire again.

**Verdict: strip, not justify.** Both call sites were removed (`scripts/odds_poll.py`'s
`find_new_value_bets()` + its `main()` alert block; `src/settlement.py`'s
`send_settlement_alert()` + its one call site). With both call sites gone, `src/betting/alerts.py`,
`kelly.py`, and `__init__.py` had zero remaining live references — confirmed by exhaustive grep
across `src/`, `backend/`, `scripts/` — and were archived to `V1_archive/src/betting/`.
`src/betting/` no longer exists as a directory in the live tree. Full trace recorded in
`ADOPTION.md`'s dated section for these two files.

This pass re-confirmed the strip was clean: `grep -rn "src.betting\|from src\.betting" src/
backend/ scripts/` returns nothing outside comments, and `odds_poll.py`/`settlement.py` import
and run correctly post-strip (verified via the scheduler's live jobs continuing to execute
`job_fetch_odds`/`job_live_settle`/`job_fetch_results` without error).

## Item 2: resurrection paths — verified dismantled, not just relocated

Each of the three paths named in the directive was independently re-traced this pass (not
assumed from D-phase's narrative):

**`run_continuous_cycle`'s admin API trigger.** The trigger logic lived in
`V1_archive/backend/app.py`'s `start_scheduler()` (a log-line conditional on job IDs), not a Flask
route as initially hypothesized. Verified at every layer:
- File itself: archived (`V1_archive/backend/app.py`), zero live importers
  (`grep -rn "from backend.app import\|import backend.app" src/ backend/ scripts/` → only a
  comment in `backend/config.py`).
- Job registration: `backend/scheduler.py` — the live V2-owned scheduler — has no
  `job_run_continuous_cycle` function at all; the only two hits for `run_continuous_cycle` in
  that file are comment lines noting it's "handled by ExecutionRuntime" (i.e., explicitly *not*
  handled by the live scheduler).
- No systemd unit or cron line references it.

**`bootstrap_system()`.** No longer exists as a function anywhere in the live tree — removed
entirely during D2's "strip resurrection paths" pass. `grep -rn "bootstrap_system" src/ backend/
scripts/` returns zero hits (not even a comment).

**`ExecutionEngine`.** Zero live imports or instantiations of either
`backend/execution_engine.py`'s or `src/betting/execution_engine.py`'s classes (both archived).
The only live-tree hit is a comment in `src/calibration/calibrator_fitting.py` noting the dead
`ExecutionEngine` dispatcher machinery was archived with V1 — a historical reference, not a
resurrection risk.

A broader identifier sweep (`AgentCoordinator`, `ExecutionRuntime`, `from src.agents`,
`from src.governance`, `from src.portfolio`) found nine live-tree files with hits; all nine were
individually inspected and every hit is a comment or docstring referencing archived history for
context (e.g. `V2ExecutionRuntime`'s own docstring explaining it replaces `AgentCoordinator`,
`backend/scheduler.py` noting core execution "moved to ExecutionRuntime"). None is a live import,
call, or instantiation of the archived classes themselves.

**New finding this pass: `bootball.service`.** A third systemd unit file, undiscovered by D8's
original sweep, was found at `/etc/systemd/system/bootball.service` — `Description=Bootball Web
UI`, pointing at archived `scripts.web_ui:app` via gunicorn on port 5000, the same port the live
`bootball-web-v2.service` uses. Last modified 2026-04-16 (predates this phase entirely — an old
forgotten duplicate, not something the D-phase work introduced). Verified `disabled`/`inactive`
via `systemctl`, and absent from `multi-user.target.wants/`, so it had zero live effect. But a
disabled-but-present unit file pointing at archived code and squatting on the live service's port
is exactly the "relocated, not dismantled" pattern item 2 warns against. Resolved this pass:
archived verbatim to `V1_archive/ops/bootball.service`, removed from `/etc/systemd/system/`,
`systemctl daemon-reload` run. Documented in `V1_archive/ops/systemd_units_removed.md`.
Post-removal, `multi-user.target.wants/` contains only the two legitimate V2 units.

**Conclusion: all three named resurrection paths, plus the newly-found fourth (`bootball.service`),
are dismantled — the target code is archived, nothing live imports or registers it, and no
systemd/cron surface can invoke it.**

## Item 3: manual-retraining gap — logged as a known, deliberate, unfixed gap

With `scripts/web_ui.py` archived (D7c), there is no file left anywhere in the live tree that
calls `Trainer.train_market()` — not disabled, not gated, simply absent. This is a real product
gap: there is currently no way to manually trigger a model retrain in V2. It is explicitly **not**
being fixed as part of this archival pass, per direct instruction. Recorded here for visibility:

- **Unaffected:** automatic drift-triggered recalibration
  (`LeagueCalibrationEngine.fit_all()`, fired via `CALIBRATION_DRIFT_DETECTED` from
  `prediction_cycle.py`'s live drift monitor) is a fully separate path and continues to run.
- **Affected:** there is no operator-facing manual retrain trigger of any kind in V2 today.
- **Disposition:** deliberate, accepted gap. Building a manual-retrain trigger is a V2 UI product
  decision for later, not an archival-phase deliverable. No action taken this phase.

## Fresh reachability graph: results

- **Live-reachable Python files** (entry points + everything they import, traced by hand):
  `v2_runtime.py`, `web_ui_v2.py`, `backend/scheduler.py`, `backend/runtime_mode.py`,
  `backend/runtime/execution_watchdog.py`, `backfill_cron.py`, `daily_run.py`, `odds_poll.py`,
  `daily_sanity_check.py`, `settlement.py`, `prediction_cycle.py`,
  `unified_prediction_service.py`, plus their shared `src/` dependencies (storage, ingestion,
  calibration, events, infra, security, notifications/v2_discord_notifier). No V1-flavored module
  appears in this set.
- **V1-flavored identifier sweep:** every hit outside `V1_archive/` is a comment or docstring
  referencing archived history for context; zero live imports, calls, or instantiations found.
- **Directory-emptiness:** `src/betting/`, `src/agents/`, `src/governance/`, `src/portfolio/`
  no longer exist in the live tree (all `git mv`'d to `V1_archive/` with mirrored paths).
- **One residual, precisely characterized:** `unified_prediction_service.py` computes a `kelly`
  value (`kelly = max(0, (b * p_blended - q) / b) * 0.25 if b > 0 else 0`) and stores it in an
  in-memory dict for the duration of `save_predictions()`, but never writes it to any DB column —
  `PredictionRecord` (the only table `save_predictions()` writes to) has no kelly-named field,
  confirmed by reading the function's full body (no `record.kelly` or equivalent assignment
  exists) and `PredictionRecord`'s column list (`kelly_fraction` exists only on the separate,
  read-only-queried `PlacedBet` table). This is vestigial computation — CPU cost paid, value
  discarded — not live execution and not V1 residue; it is reported here precisely as that,
  distinct from a genuine finding, and left as-is (removing it is a minor efficiency cleanup, not
  an archival-scope or correctness concern).
- **`BankrollRound`/`PlacedBet` ORM classes:** still defined in `src/storage/models.py`, still
  queried (read-only — historical `SELECT`s) by live code. Zero live `PlacedBet(...)` constructor
  calls found anywhere. Consistent with D-phase's prior finding that the betting ledger has taken
  no new rows since 2026-06-07 while `bot_enabled=False`.
- **Test collection safety:** `pytest --collect-only -q` succeeds cleanly across all ten test
  files with zero collection errors — no import-time breakage from any Part D/E move.

## Overall verdict

Part D's incremental archival held up under a from-scratch reachability recomputation. One gap
existed at the seam between D7c and Part E (the `bootball.service` unit file, predating this
phase and missed by D8's original sweep) and has been closed this pass. The `odds_poll.py`
entanglement flagged as open at D7c is now resolved by strip, with the full trace on record in
`ADOPTION.md`. All three explicitly-named resurrection paths, plus the fourth found during this
pass, are confirmed dismantled at every layer checked (file presence, live imports, job
registration, systemd/cron wiring). The manual-retrain gap is logged as accepted and deliberate.
Phase 31's V1/V2 separation is complete as of this audit.
