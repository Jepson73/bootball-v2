# Phase 31 Part D — Progress Checkpoint

Kept current at every commit during D10's cutover so a dropped session can resume without
re-deriving state. Not a design doc — see `OWNERSHIP.md` for that. This is just "what's done,
what's next."

## Done (committed, verified live)

- D0–D7: extraction, resurrection-path removal, relocations (betting→prediction/lib,
  governance→infra, alerts/event_bus→events), DEAD/UNCLEAR + V1-thesis archival. See git log.
- D8 (complete): dead `auto_bet.py --bet-only` / `settle_fixtures.py` cron lines preserved in
  `V1_archive/ops/cron_bootball_removed_entries.md`, removed from live `/etc/cron.d/bootball`.
  Unit-file half: `bootball-runtime.service`/`bootball-web.service` copied verbatim (diffed
  byte-identical before removal) to `V1_archive/ops/`, alongside `systemd_units_removed.md`
  explaining why/how to restore; removed from `/etc/systemd/system/`, `daemon-reload`d.
  `systemctl cat` for both now reports "No files found"; `list-units --all 'bootball*'` shows
  only the two V2 units. `bootball-v2-runtime.service`'s own `Description=` field updated to
  drop stale "Phase 31 Part C — parallel-verification window" wording (metadata-only edit,
  `daemon-reload` without restart — service stayed active throughout).
- D9: `V2ExecutionRuntime.start()` now calls `backend.scheduler.start_scheduler()`. Shipped
  inert — landed while `bootball-v2-runtime.service` was still running old code, so no double
  scheduler was registered. Activates on this service's next restart.

## D10 — COMPLETE (2026-07-08)

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
3. [x] Verified as jobs naturally fired, from V2 alone, no double-claim: `fetch_results`
       completed 2026-07-07 ~19:xx UTC and every hour since (confirmed again 2026-07-08
       16:27:43 UTC and 17:21:14 UTC in `journalctl -u bootball-v2-runtime.service`);
       `fetch_fixtures` completed on its 6h cadence (confirmed 2026-07-08 16:28:48 UTC,
       "next run at: 2026-07-08 22:21:14 UTC"); V2 prediction cycle cadence confirmed
       continuing (cycle #1 at 18:17:06 UTC 07-07, calibration report generated
       2026-07-08 16:35:37 UTC); port 5001 dark, port 5000 serving throughout.
4. [x] Negative checks, confirmed 18:17 UTC: `ps aux` sweep for execution_runtime.py/
       coordinator/gunicorn-web_ui:app → zero matches. `apscheduler_jobs` table has exactly
       7 rows, one per job id (structurally single-claimant — only one process is running).
       V1 Discord already silenced pre-D10 (Phase 30); log confirms
       "V1 Discord-only consumers ... not registered — discord_v1_enabled=False".
5. [x] The 02:00 UTC `daily_run.py` cron gate completed cleanly under the new topology.
       `/var/log/bootball/daily_run.log` mtime 2026-07-08 02:05:51 UTC, last line
       "Pipeline succeeded: 0 errors in 345.5s" — 1316 backfilled, 1476 upcoming, 12 settled,
       run_id `1b48d76c-f735-42d7-8bbf-6827419c578a`. V1 web/runtime dark throughout, V2 owned
       the aux scheduler the whole run.
6. [x] deploy.sh's service list, docs/deployment_state.md, and docs/codebase_reference.md
       done and committed (d5d3a49, 39bcbe6) — SERVICES array trimmed to the two V2 units;
       deployment_state.md's systemd/cron/log sections mark V1 retired; codebase_reference.md's
       Entry Points, Startup Sequence, execution_runtime.py/coordinator.py sections, and the
       Fixture→Prediction data-flow diagram all now describe V2 as sole execution authority.
7. [x] Reboot-survival test passed. Host rebooted 2026-07-08 04:21 UTC (scheduled window).
       `bootball-v2-runtime.service` self-started same boot (`ExecMainStartTimestamp`
       2026-07-08 04:21:10 UTC), ran from commit `971da2092` (correct HEAD at boot time),
       re-claimed all 7 aux jobs ("Scheduler started with jobs: [...7...]"), V2 Discord wired
       ("✅ V2 Discord notifications active"). `systemctl is-enabled`/`is-active` for both V1
       units confirmed `disabled`/`inactive` post-reboot — they did not resurrect. `ps aux`
       sweep for execution_runtime/coordinator/gunicorn-web_ui:app: zero matches.

**D10 checkpoint closed.** All 7 steps verified live. Continuing into D8's remaining unit-file
archival, D7c, and Part E per the user's "same momentum" authorization.

## Not started yet

- D7c: archive coordinator.py + its ~26 remaining dependents (src/agents/*, remaining
  src/betting/*, remaining src/governance/*, performance_tracker.py, src/portfolio/*,
  betting_state.py, system_truth_snapshot.py, web_ui.py) — gated on D10 completing (now done),
  re-verify the reachability graph post-cutover before moving anything.
- Part E: `AUDIT_V2_STANDALONE.md` — standalone re-audit of V2, follows immediately per the
  user's "don't let a gap open between D and E."
