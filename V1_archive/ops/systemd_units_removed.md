# Removed /etc/systemd/system unit files — Phase 31 Part D (D8, unit-file half)

Preserved verbatim (`bootball-runtime.service`, `bootball-web.service` in this directory)
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

## Restoring, if ever needed

Copy the `.service` file back to `/etc/systemd/system/`, `systemctl daemon-reload`,
`systemctl enable --now <unit>`. Note `bootball-web.service` and `bootball-runtime.service`
would then be racing `bootball-v2-runtime.service`/`bootball-web-v2.service` for the same
`backend/scheduler.py` jobstore (`data/scheduler.db`) and the same betting-pipeline side
effects — see D9's inert-shipping note in `OWNERSHIP.md` for why that's unsafe to do casually.
