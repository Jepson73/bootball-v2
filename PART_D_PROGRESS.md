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
1. [ ] Stop + disable `bootball-runtime.service` and `bootball-web.service` in the same motion.
       Verify `systemctl is-enabled` reports `disabled` for both (not just stopped — tonight has
       a scheduled reboot, and stopped-but-enabled resurrects on boot).
2. [ ] Restart `bootball-v2-runtime.service` to pick up D9's scheduler-ownership code. Confirm
       via logs that it *claims* the jobs (scheduler registration lines: "Added auxiliary job: ..."
       for fetch_fixtures/fetch_results/fetch_odds/cleanup_matches/live_settle/
       daily_sanity_check/v2_collection_heartbeat), not just that the process starts.
3. [ ] Verify in sequence as jobs naturally fire:
       - odds_trajectory_scheduler's next cycle (cron, unaffected by any of this — sanity check)
       - fetch_results' next hourly run, from V2's scheduler
       - V2 prediction cycle cadence continuing unbroken (20-min cycles)
       - port 5001 dark, port 5000 still serving
4. [ ] Negative checks: zero V1 processes (`ps` sweep for coordinator/execution_runtime), no V1
       Discord messages, no scheduler double-fire (only one process claims each job id).
5. [ ] Tonight's 02:00 UTC `daily_run.py` cron entry is the final gate — this is the one path
       intentionally left alone (still root/cron, not touched by D10). Confirm it completes
       normally under the new topology (V1 web/runtime dark, V2 owns the aux scheduler).
6. [ ] Commit the checkpoint once 1–5 hold, then continue into D7c (archive coordinator.py's
       remaining dependents), deploy.sh's service list, and Part E's standalone audit.
7. [ ] Tomorrow's ~04:00 UTC scheduled reboot is a free reboot-survival test: on reconnect,
       verify via deploy.sh's check + V2 deploy/heartbeat notifications that the V2-only set
       self-started on the correct commit and both V1 units stayed dark (disabled, not just
       stopped-until-now). This is Part D's closing evidence.

## Not started yet

- D7c: archive coordinator.py + its ~26 remaining dependents (src/agents/*, remaining
  src/betting/*, remaining src/governance/*, performance_tracker.py, src/portfolio/*,
  betting_state.py, system_truth_snapshot.py, web_ui.py) — gated on D10 completing, re-verify
  the reachability graph post-cutover before moving anything.
- deploy.sh service list update (remove bootball-runtime.service / bootball-web.service).
- docs/deployment_state.md update.
- Part E: `AUDIT_V2_STANDALONE.md` — standalone re-audit of V2, follows immediately per the
  user's "don't let a gap open between D and E."
