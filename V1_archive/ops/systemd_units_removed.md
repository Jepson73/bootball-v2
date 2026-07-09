# Removed /etc/systemd/system unit files — Phase 31 Part D (D8, unit-file half)

Preserved verbatim (`bootball-runtime.service`, `bootball-web.service`, `bootball.service` in this directory)
rather than deleted, per the "move not delete" principle applied to the cron entries
(`cron_bootball_removed_entries.md`, same directory) and to code elsewhere in this archive.

## Why removed

Both units were V1's long-running processes: `bootball-runtime.service` ran
`backend/runtime/execution_runtime.py` (`AgentCoordinator.run_cycle()` every 20 minutes),
`bootball-web.service` ran `scripts/web_ui.py` (Flask + embedded APScheduler) via gunicorn on
port 5001. Phase 31 Part D's D10 cutover (2026-07-07 18:1x UTC) moved sole execution authority
to `bootball-v2-runtime.service` — see `OWNERSHIP.md`'s "Key finding" for why V1's pipeline was
~97% dead theater by that point, and `PART_D_PROGRESS.md` for D10's step-by-step live evidence.

Both units were stopped **and** disabled at D10 (not stopped-only — a stopped-but-enabled unit
would have been resurrected by the host's next scheduled reboot). That reboot landed
2026-07-08 04:21 UTC and served as a free reboot-survival test: `systemctl is-enabled`/
`is-active` for both confirmed `disabled`/`inactive` post-reboot, and a `ps aux` sweep found
zero V1 processes. Once that evidence was in, removing the unit files themselves from
`/etc/systemd/system/` was just cleanup — the units had already had zero effect for over a day.

## Third unit found post-cutover: `bootball.service` (removed 2026-07-09, Part E)

D8's original sweep found and removed `bootball-runtime.service`/`bootball-web.service` but
missed a third unit, `bootball.service` — undiscovered until Part E's fresh reachability audit
went looking for anything that could still reach archived V1 code. Its `ExecStart` ran gunicorn
against `scripts.web_ui:app` (the same archived Flask app `bootball-web.service` ran) bound to
**port 5000** — the exact port `bootball-web-v2.service` (the live V2 web service) listens on.
Last modified 2026-04-16, predating this whole phase — an old, forgotten duplicate, not something
introduced during the D-phase work.

Verified `disabled`/`inactive` via `systemctl is-enabled`/`is-active`, and confirmed absent from
`/etc/systemd/system/multi-user.target.wants/` (only the two live V2 units are symlinked there),
so it had zero live effect. But per the Part E standard — resurrection paths must be dismantled,
not just relocated — a disabled-but-present unit file pointing at archived code and squatting on
the live service's port is exactly the kind of loose end that standard exists to catch. Archived
verbatim to this directory, removed from `/etc/systemd/system/`, `systemctl daemon-reload` run.
Post-removal, `multi-user.target.wants/` contains only `bootball-v2-runtime.service` and
`bootball-web-v2.service`.

## Restoring, if ever needed

Copy the `.service` file back to `/etc/systemd/system/`, `systemctl daemon-reload`,
`systemctl enable --now <unit>`. Note `bootball-web.service` and `bootball-runtime.service`
would then be racing `bootball-v2-runtime.service`/`bootball-web-v2.service` for the same
`backend/scheduler.py` jobstore (`data/scheduler.db`) and the same betting-pipeline side
effects — see D9's inert-shipping note in `OWNERSHIP.md` for why that's unsafe to do casually.
