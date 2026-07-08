# Phase 31 Part D — Progress Checkpoint

Kept current at every commit during D10's cutover so a dropped session can resume without
re-deriving state. Not a design doc — see `OWNERSHIP.md` for that. This is just "what's done,
what's next."

## Done (committed, verified live)

- D0–D7: extraction, resurrection-path removal, relocations (betting→prediction/lib,
  governance→infra, alerts/event_bus→events), DEAD/UNCLEAR + V1-thesis archival. See git log.
- D8 (cron half): dead `auto_bet.py --bet-only` / `settle_fixtures.py` cron lines preserved in
  `V1_archive/ops/cron_bootball_removed_entries.md`, removed from live `/etc/cron.d/bootball`.
  Unit-file half (bootball-runtime.service, bootball-web.service → `V1_archive/ops/`) waits here.
- D9: `V2ExecutionRuntime.start()` now calls `backend.scheduler.start_scheduler()`. Shipped
  inert — landed while `bootball-v2-runtime.service` was still running old code, so no double
  scheduler was registered. Activates on this service's next restart.

## D10 — in progress (this checkpoint)

Units before D10: `bootball-runtime.service` (active, enabled), `bootball-web.service` (active,
enabled, port 5001), `bootball-v2-runtime.service` (active, enabled, old code — no scheduler),
`bootball-web-v2.service` (active, enabled, port 5000).

Steps, in order:
1. [x] Stop + disable `bootball-runtime.service` and `bootball-web.service`. Done 2026-07-07
       18:1x UTC. Confirmed `systemctl is-enabled` → `disabled` for both; neither appears in
       `systemctl list-units --all 'bootball*'` at all post-disable.
2. [x] Restart `bootball-v2-runtime.service` (18:17:04 UTC, commit 2e62d55). Logs show it
       claiming all 7 jobs ("Added auxiliary job: ..." for fetch_fixtures/fetch_results/
       fetch_odds/cleanup_matches/live_settle/daily_sanity_check/v2_collection_heartbeat),
       then "Added job ... to job store default" x7, "Scheduler started with jobs: [...7...]".
       Prediction cycle #1 started immediately after (1405 NS fixtures fetched).
3. [ ] Verify as jobs naturally fire (in progress — logged times to watch for):
       - fetch_results: next hourly fire ~19:17 UTC or later
       - fetch_fixtures: next 6h fire
       - odds_trajectory_scheduler: cron, unaffected, ~20 min (sanity check only)
       - V2 prediction cycle cadence: confirmed continuing (cycle #1 at 18:17:06 UTC)
       - port 5001 dark (confirmed — ss shows nothing bound), port 5000 serving (confirmed)
4. [x] Negative checks, confirmed 18:17 UTC: `ps aux` sweep for execution_runtime.py/
       coordinator/gunicorn-web_ui:app → zero matches. `apscheduler_jobs` table has exactly
       7 rows, one per job id (structurally single-claimant — only one process is running).
       V1 Discord already silenced pre-D10 (Phase 30); log confirms
       "V1 Discord-only consumers ... not registered — discord_v1_enabled=False".
5. [ ] Tonight's 02:00 UTC `daily_run.py` cron entry is the final gate — this is the one path
       intentionally left alone (still root/cron, not touched by D10). Confirm it completes
       normally under the new topology (V1 web/runtime dark, V2 owns the aux scheduler).
6. [~] deploy.sh's service list, docs/deployment_state.md, and docs/codebase_reference.md are
       done and committed (d5d3a49, 39bcbe6) — SERVICES array trimmed to the two V2 units;
       deployment_state.md's systemd/cron/log sections mark V1 retired; codebase_reference.md's
       Entry Points, Startup Sequence, execution_runtime.py/coordinator.py sections, and the
       Fixture→Prediction data-flow diagram all now describe V2 as sole execution authority
       instead of V1/parallel-window language. Full D10 checkpoint (this line → [x]) still
       waits on steps 3 and 5 below, then D7c and Part E follow.
7. [ ] Tomorrow's ~04:00 UTC scheduled reboot is a free reboot-survival test: on reconnect,
       verify via deploy.sh's check + V2 deploy/heartbeat notifications that the V2-only set
       self-started on the correct commit and both V1 units stayed dark (disabled, not just
       stopped-until-now). This is Part D's closing evidence.

## Not started yet

- D7c: archive coordinator.py + its ~26 remaining dependents (src/agents/*, remaining
  src/betting/*, remaining src/governance/*, performance_tracker.py, src/portfolio/*,
  betting_state.py, system_truth_snapshot.py, web_ui.py) — gated on D10 completing, re-verify
  the reachability graph post-cutover before moving anything.
- Part E: `AUDIT_V2_STANDALONE.md` — standalone re-audit of V2, follows immediately per the
  user's "don't let a gap open between D and E."
