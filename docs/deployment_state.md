# Bootball — Deployment State Reference
**Last updated:** 2026-07-09 (Phase 31 Part E — standalone audit + close-out sweep)  
**Purpose:** Capture out-of-repo operational state so the system can be reproduced if the host is lost.

**Phase 31 complete (Parts A–E).** `bootball-runtime.service` and `bootball-web.service` (V1),
plus a third undocumented unit `bootball.service` found during Part E, are all stopped, disabled,
and removed from `/etc/systemd/system/` — archived verbatim to `V1_archive/ops/`.
`bootball-v2-runtime.service` is the sole execution authority: it owns both the 20-min prediction
cycle and the auxiliary APScheduler jobs V1 used to run. This doc now describes the live V2-only
layout throughout — see `OWNERSHIP.md`, `PART_D_PROGRESS.md`, and `AUDIT_V2_STANDALONE.md` for
the full record of how it got here.

---

## Required Post-Commit Step: `scripts/deploy.sh`

**Committed code does not run until the owning service restarts.** systemd does not watch
files — `bootball-runtime.service` and `bootball-web-v2.service` keep executing whatever was
in memory at process start, indefinitely, until something restarts them. The only thing that
restarted them before this script existed was the host's own daily reboot (~04:00–04:20 UTC,
outside our control — see "Host reboots daily" below) or an ad-hoc manual `systemctl restart`.

This has caused real incidents four times: H2H vector persistence (Phase 11b/13c), the
CACHE_DIR fix (Phase 11b — ran stale/broken for ~16.5h, ~62k failed cache writes before the
next reboot picked it up), V1 predictions reappearing, and — most severely — Phase 21's h2h
notation fix, void handling, `resync_stale_fixtures`, and the auto-dead rule all sitting
committed-but-inert for up to 18 hours on 2026-07-01. Audit table:

| Fix | Committed | Service last restarted before | Inert window | Corrupted anything while inert? | Live now? |
|---|---|---|---|---|---|
| CACHE_DIR path (Phase 11b) | 06-29 11:49:57 | 06-29 04:20:07 → next restart 06-30 04:19:24 | ~16.5h | Yes — ~62,270 `PermissionError` cache-write failures logged 06-29 11:00–06-30 05:00 (journalctl). Each hit a live API call instead of cache, but no bad data was written (fetch just failed and was retried) | Yes — confirmed via journal (errors dropped from 259/hr to a residual 2 stray root-owned files/hr, unrelated pre-existing files, not this bug) |
| H2H vector persistence (Phase 13c) | 07-01 07:22:20 | 06-30 04:19:24 → next restart 07-01 18:52:21 | ~11.5h | No — the 988 pre-existing NULL-vector records it was meant to backfill just stayed NULL a bit longer; the one-time A2 backfill script was run manually with the fixed code already in the tree, ahead of the restart | Yes — `SELECT` on records created after 18:53 shows 0/73 with NULL `prob_home` |
| h2h notation fix, void handling, `resync_stale_fixtures`, auto-dead rule (Phase 21+22) | 07-01 17:38:05 / 18:45:29 | 07-01 04:20:48 → next restart 18:52:21 / 18:53:31 | ~1h–18h depending on sub-fix | No confirmed corruption — DB check found zero `elo_hybrid` h2h predictions with a real `settled_at` timestamp before 17:23 (when the manual fix+cleanup was applied by hand, ahead of the commit and restart); no fixture had gone final yet, so the bug was genuinely caught before any live settlement used it | Yes — confirmed via today's 02:00 UTC cron `daily_run.py` log (`resync_stale_fixtures: checked 30, updated 4...`) and 16 fixtures in DB with `status='DEAD'` |
| Soft-odds display (Phase 18+19) | 07-01 12:39:32 | web-v2 running since before 12:39 → restarted 18:14:11 same day | ~5.5h | No — display-only, no data written; viewers just saw stale UI, not a data bug | Yes — confirmed via curl against live port (see Task 3 below) |

Full detail in commit messages `5c572b6` and `3b2a9a2`, which document this same investigation.

**Rule: after any commit that touches `backend/runtime/`, `src/`, `v2/`, or `scripts/web_ui*.py`,
run `scripts/deploy.sh`.**

```bash
scripts/deploy.sh          # restart every long-running service, verify each comes back active
scripts/deploy.sh check    # report staleness (self-reported commit vs current HEAD) — no restart
```

### What it covers

All long-running processes that import and execute this repo's code in-process (i.e. everything
that can go stale — cron-triggered scripts cannot, see below):

| Service | Runs | Restart needed when... |
|---|---|---|
| `bootball-v2-runtime.service` | `backend/runtime/v2_runtime.py` — 20-min prediction cycle (`src.prediction.prediction_cycle`) + APScheduler (fetch_fixtures/results/odds, cleanup, live_settle, daily_sanity_check, v2_collection_heartbeat) since Phase 31 D9/D10 | anything under `src/`, `backend/`, or `config/` changes |
| `bootball-web-v2.service` | `scripts/web_ui_v2.py` (port 5000) | anything under `v2/` or `src/` changes |

**Retired at Phase 31 Part D cutover (2026-07-07):** `bootball-runtime.service` and
`bootball-web.service` (V1) — stopped + disabled, no longer in scope for `deploy.sh`. Their unit
files are archived verbatim at `V1_archive/ops/bootball-{runtime,web}.service` (D8, 2026-07-08)
and no longer exist under `/etc/systemd/system/`; see `scripts/deploy.sh`'s `SERVICES` array for
the current list.

**Not in scope, and cannot go stale:** `daily_run.py`, `odds_poll.py`, `odds_trajectory_scheduler.py`,
`backfill_cron.py`, `probe_forward_odds.py` — all cron-triggered (`/etc/cron.d/bootball`,
`crontab -l`). Each invocation is a fresh `python3` process that reads whatever is on disk at
that moment, so they're automatically current after any commit. No restart mechanism needed or
possible for these. (`auto_bet.py --bet-only` and `settle_fixtures.py`'s cron entries were removed
at Phase 31 D8 — both were confirmed dead; see `V1_archive/ops/cron_bootball_removed_entries.md`.)

### Detecting a stale service

`bootball-v2-runtime.service` and `bootball-web-v2.service` self-report the git commit they started
from via `src/deploy_info.py` → `logs/deploy_state/<service>.running_commit`, written at process
startup regardless of *how* the process was restarted (deploy.sh, manual `systemctl restart`, or
the daily host reboot).

`scripts/deploy.sh check` compares these against current `HEAD` and reports up-to-date / stale
(with commit count behind) per service.

**Requires `git config --system --add safe.directory /opt/projects/bootball`** — the `bootball`
system user could not otherwise run `git rev-parse HEAD` inside a repo it doesn't own (`.git` is
root-owned); without this, `record_running_commit()` fails silently (logs a warning, service still
starts) and staleness detection falls back to the weaker deploy.sh-record signal. Already applied
on this host as of 2026-07-02; re-apply on fresh deploy (see checklist below).

### Host reboots daily

The VM itself reboots once a day around 04:00–04:20 UTC (host-level, outside this repo's control —
observed via `last reboot`, not a cron job in this repo). This is what silently "fixed" every prior
deploy-gap incident by the next morning — which is also why they went unnoticed for so long. Do not
rely on it; always run `scripts/deploy.sh` after committing.

**Found 2026-07-09 (Part E close-out sweep): the daily reboot also silently breaks both
"silence means broken" guards — neither is actually alive right now.**

1. **`v2_collection_heartbeat` (backend/scheduler.py) has structurally never fired since D9/D10
   gave it to V2 (2026-07-07).** It's a 24h-interval APScheduler job registered with
   `replace_existing=True` and no explicit `start_date`; every `add_job()` call recomputes
   `next_run_time` as roughly *now + 24h* from the moment of that call. The host reboots every
   ~23h38m–23h40m (confirmed via `last reboot` — always a few minutes short of 24h), and each
   reboot restarts `bootball-v2-runtime.service`, which re-registers the job and pushes
   `next_run_time` out another 24h — always just out of reach. Confirmed directly against
   `data/scheduler.db`'s `apscheduler_jobs` table on 2026-07-09 13:07 UTC:
   `next_run_time = 2026-07-10 04:20:44`, i.e. exactly 24h after that morning's 04:20:44 restart,
   and `journalctl -u bootball-v2-runtime.service` shows the job being *registered* on every
   restart (07-07, 07-08, 07-09) but never once *firing* ("JOB: v2_collection_heartbeat starting"
   never appears in the log). Silence from this channel currently means nothing — it hasn't run,
   not "ran and found nothing to report."
2. **`daily_sanity_check` has the identical bug** (same 24h interval, same `replace_existing=True`
   registration pattern) — `next_run_time` also sits at `2026-07-10 04:20:44` as of this sweep.
3. **`notify_deploy_complete` (the deploy-confirmation notification, fired only from inside
   `scripts/deploy.sh`) has not fired since 2026-07-04 20:34** — `logs/deploy_state/*.commit`
   (the marker `deploy.sh` itself writes mid-run, distinct from the `*.running_commit` files each
   service self-reports at any process startup) is untouched since then. Every restart since —
   including the D10 cutover restart and every subsequent daily reboot — happened via direct
   `systemctl restart`/host reboot, not via `scripts/deploy.sh`, so this notification was never
   invoked for any of it. (The code itself has stayed current throughout — `running_commit` is
   self-reported independently of `deploy.sh` — this is specifically about the confirmation
   notification never being sent, not about stale code.)

**Not fixed as part of this sweep** — flagged for a deliberate decision, since the fix likely
involves either giving the interval trigger a stable `start_date`/switching to a `cron` trigger
anchored to a fixed daily time (immune to restart timing), or accepting `scripts/deploy.sh` as
the only path that ever confirms a deploy and enforcing that it's actually run.

---

## System Layout

| Item | Value |
|------|-------|
| Repo root | `/opt/projects/bootball` |
| Venv | `/opt/projects/bootball/.venv` |
| System user | `bootball` (uid=999, gid=996) |
| DB file | `data/db/bootball.db` (gitignored) |
| API cache (inner, writable) | `data/raw/api_cache/api_cache/` — `bootball:bootball drwxrwxrwx` |
| API cache (outer, legacy) | `data/raw/api_cache/` — `nobody:nogroup drwxr-xr-x` ← **do not use** |
| System log dir | `/var/log/bootball/` — owned root, created manually |
| Quota log | `logs/quota_log.csv` (inside repo root, gitignored via `logs/`) |
| Env file | `/opt/projects/bootball/.env` (gitignored — see `.env.example`) |
| V1 archive | `V1_archive/` (in repo, tracked in git — not gitignored) — 96 `.py` files, ~1.7MB, mirroring their original live-tree paths under `V1_archive/{src,backend,scripts,tests,dead,ops}/`. Everything V1-only that Phase 31 identified as dead: `src/agents/`, `src/governance/` (old), `src/portfolio/`, the old `backend/execution_engine.py`/`execution_runtime.py`, `scripts/web_ui.py`, `scripts/make_predictions.py`, `src/betting/{alerts,kelly}.py`, plus `V1_archive/ops/` for removed systemd unit files and cron lines. Nothing here runs; it exists for reference/possible restoration only. See `OWNERSHIP.md`/`PART_D_PROGRESS.md`/`AUDIT_V2_STANDALONE.md` for what moved when and why. |

---

## Critical CACHE_DIR Fix

**Without this, the scheduler exhausts the 75k/day API-Football quota by 15:00 UTC daily.**

`src/ingestion/client.py` line 26:
```python
CACHE_DIR = Path("data/raw/api_cache/api_cache")   # INNER dir — bootball-owned, writable
```

The outer `data/raw/api_cache/` directory is owned by `nobody:nogroup` and is not writable by the `bootball` process. All cache writes must go to the inner subdirectory. If `CACHE_DIR` is ever reset to `Path("data/raw/api_cache")` (without the second `api_cache`), every hourly scheduler run will make 2,480 uncached API calls and exhaust the daily quota before 16:00 UTC.

**On fresh deploy:** run `mkdir -p data/raw/api_cache/api_cache` as the `bootball` user to ensure the inner dir exists and is writable.

**Odds endpoint exception (Phase 25):** `get_odds()` sets `force_refresh=True` unconditionally
— it must never be served from this cache, since odds are the one endpoint where "the same
answer as last time" is a stale price, not a saved call. See "Odds Trajectory Capture" below.

---

## Post-separation quota baseline (Phase 31 Part E, measured 2026-07-09)

Full-day totals from `logs/quota_log.csv` (`calls_used` resets to ~0 at UTC midnight; last row of
the day ≈ that day's total spend): 07-06 (pre-cutover) 60,754 · 07-07 (cutover day, mixed)
50,386 · **07-08 (first full V1-stopped day) 64,272** · 07-09 (in progress) 33,717 as of 12:30 UTC.

**No drop materialized, and none was mechanically expected.** This doc already records why
(see `bootball-runtime.service`'s entry above): `AgentCoordinator.run_cycle()` made **0 API
calls** even before it was stopped — its live component was a DB read (fetch `Fixture` rows) plus
`UnifiedPredictionService`, not an external fetch. `OWNERSHIP.md`'s Part C finding is the same:
~97% of the V1 cycle was self-referential dead-portfolio computation, and the ~3% live remainder
never touched the API-Football client. Stopping V1 therefore removed zero API-call load by
construction — there was no duplicate real-time fetch path to eliminate.

Actual daily spend is, and was, driven by the shared ingestion pipeline V2 now owns outright:
`daily_run.py`'s hourly `DailyBaselinePipeline` run (~1,500–3,000 calls per run, ~24 runs/day),
`odds_trajectory_scheduler.py`'s half-hourly captures (~100–800 calls per tick), and
`backfill_cron.py`'s daily 09:00 pass (now down to single-digit calls most days — the historical
backfill campaign is effectively finished, not the ~50k/day driver it was earlier in the project;
see `project_quota_timeline` — that prediction undershot how long full completion actually took).

**Recorded baseline for future anomaly detection: ~55,000–65,000 calls/day steady state**
post-separation, dominated by real-time fixture/odds refresh cadence, not backfill and not any V1
residue. A future reading meaningfully above ~70k or below ~40k on a day with normal fixture
volume is the signal worth investigating, not a return to some lower pre-cutover number — no such
lower number exists in this system's actual history once backfill's decline is accounted for.

### Quota decomposition — 07-08's 64,272 broken down by component (2026-07-09)

64,272 / 75,000 is ~86% utilization on an otherwise-ordinary day. Decomposed rather than just
recorded, since at that utilization a weekend fixture spike is a real risk of hitting the ceiling,
and this system has been burned by unexplained spend before (the CACHE_DIR permission bug, the
cron/systemd UID split — see the incident table and Part C's UID-split finding above).

| Component | Calls | Share | Method |
|---|---|---|---|
| `DailyBaselinePipeline.run()` — in-process, fired by both `job_fetch_fixtures` (6h) and `job_fetch_results` (1h) inside `bootball-v2-runtime.service` | **39,606** | **61.6%** | Exact — summed `(run_end.calls_used − run_start.calls_used)` across all 24 matched pairs in `quota_log.csv` for 07-08 |
| `odds_trajectory_scheduler.py` — cron, every 30 min, 24/7 | **12,224** | **19.0%** | Exact — script self-reports `Calls this run` (its own `near_calls + far_calls` tally, not derived from the shared counter) in `/var/log/bootball/odds_trajectory_scheduler.log`; summed across all 47 runs on 07-08 |
| Everything else — `job_fetch_odds` (in-process, 1h), `odds_poll.py`'s own cron entry (`*/30`, daytime CET), `job_live_settle`'s live-score polling (~7 calls/tick × up to 720 ticks/day), `job_fetch_results`'s post-pipeline settlement calls, `backfill_cron.py` (near-zero, see above) | **12,442** | **19.4%** | Residual (day total minus the two exact components above) — **not further separable with current instrumentation**, see caveat below |

**The dominant cost is fixture/result refresh, not backfill and not trajectory capture** — both of
which were the *a priori* suspects. `DailyBaselinePipeline.run()` alone is 61.6% of the day, and it
fires on the union of two independent schedules (`job_fetch_fixtures` @ 6h, `job_fetch_results` @
1h) that aren't coordinated to avoid overlap — `quota_log.csv` shows both landing in the same
APScheduler tick at least once on 07-08 (two `run_start` rows both timestamped 10:20:44), meaning
the pipeline can run twice back-to-back for no added freshness. **If quota pressure ever becomes a
live problem, this is the lever** — not backfill (already near-finished) and not trajectory (already
self-capped at `collection_daily_cap`=15,000/day for its daily-phase touches).

**Measurement caveat:** `quota_log.csv` and the per-script "calls remaining" reads all draw from
one shared, global, cross-process daily counter — there is no per-job API-call instrumentation.
The `DailyBaselinePipeline` figure above is a clean bracket (before/after the exact call), but if
`job_fetch_odds` happened to be mid-flight in another thread of the same process during that
bracket, its calls would leak into the pipeline's number rather than the residual. The residual
bucket is the least trustworthy line — it's four-plus distinct consumers collapsed into "whatever
wasn't inside a clean bracket," not a verified breakdown of any one of them. Good enough to confirm
none of them individually looks anomalous (the two exact components already account for ~81% of
the day, leaving a residual within the range that live-settle's own documented worst case
(~5,040/day) plus odds_poll's cron entry plausibly explain) — not good enough to catch a smaller
regression inside that bucket. Per-job quota tagging would close this gap; not built this pass (no
code change requested for this rider).

---

## SQLite busy_timeout (Phase 25)

`src/storage/db.py` sets `PRAGMA busy_timeout = 5000` on every connection. The DB is already
in WAL mode (readers don't block the writer), but 5+ independent writer processes (runtime,
odds_poll/daily_run/settle_fixtures/backfill/odds_trajectory_scheduler cron jobs) share this
file, and the default `busy_timeout=0` means two writers colliding at the same instant get an
immediate `SQLITE_BUSY` error instead of a short wait. Flagged in the Phase 24 cost scoping,
fixed here since Phase 25 adds meaningful write volume.

---

## Systemd Services

Two live service files under `/etc/systemd/system/` as of Phase 31 Part D's cutover
(2026-07-07) — `bootball-runtime.service` and `bootball-web.service` (V1) are stopped + disabled,
kept below for reference until their unit files move to `V1_archive/ops/`:

### `bootball-v2-runtime.service` — Execution engine (prediction cycle + APScheduler)
```ini
[Unit]
Description=Bootball V2 Execution Runtime (Phase 31 Part C — parallel-verification window)
After=network.target

[Service]
Type=simple
User=bootball
Group=bootball
WorkingDirectory=/opt/projects/bootball
ExecStart=/opt/projects/bootball/.venv/bin/python3 backend/runtime/v2_runtime.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**What it runs:** `backend/runtime/v2_runtime.py`  
- 20-min `src.prediction.prediction_cycle.run_prediction_cycle()` loop (V2 prediction pipeline,
  `V2_RUNTIME_WRITE_ENABLED=true` — saving live since the parity-verification window)
- Since Phase 31 D9/D10: APScheduler with the same 7 auxiliary jobs V1 used to run —
  `fetch_fixtures` (6h), `fetch_results` (1h), `fetch_odds` (1h), `cleanup_matches` (5m),
  `live_settle` (2m), `daily_sanity_check` (24h), `v2_collection_heartbeat` (24h)
- The `fetch_results` job is the primary API consumer: ~2,480 calls for the first daily run (cache fill), ~40 calls per subsequent run.
- `Restart=always` with `RestartSec=5` — **if it restarts after midnight before the first hourly run, the first run costs 2,480 calls (acceptable).**

### `bootball-runtime.service` — V1 execution engine (STOPPED + DISABLED, 2026-07-07)
```ini
[Unit]
Description=Bootball Execution Runtime
After=network.target

[Service]
Type=simple
User=bootball
Group=bootball
WorkingDirectory=/opt/projects/bootball
ExecStart=/opt/projects/bootball/.venv/bin/python3 backend/runtime/execution_runtime.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Ran `AgentCoordinator.run_cycle()` (V1 prediction pipeline, 0 API calls, `bot_enabled=False`) plus
the same auxiliary APScheduler now owned by `bootball-v2-runtime.service` above. Retired at Phase
31 Part D's cutover — see `OWNERSHIP.md`'s "Key finding" for why (~97% dead theater by the end).

### `bootball-web.service` — V1 Flask UI (STOPPED + DISABLED, 2026-07-07 — port 5001)
```ini
[Unit]
Description=Bootball Web UI (V1 — reference, port 5001)
After=network.target

[Service]
Type=simple
User=bootball
Group=bootball
WorkingDirectory=/opt/projects/bootball
Environment="PYTHONPATH=/opt/projects/bootball"
ExecStart=/opt/projects/bootball/.venv/bin/gunicorn -w 1 -b 0.0.0.0:5001 --timeout 120 scripts.web_ui:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**Phase 13 change (2026-06-30):** V1 moved from port 5000 to 5001 via gunicorn.
`scripts/__init__.py` (empty) was added to make `scripts` a Python package importable by gunicorn.
V1's `app.run(port=5000)` is inside `if __name__ == '__main__':` — gunicorn bypasses it.
Retired at Phase 31 Part D's cutover; port 5001 is dark.

### `bootball-web-v2.service` — V2 Web UI (port 5000, primary)
```ini
[Unit]
Description=Bootball Web UI V2 (two-track, port 5000)
After=network.target

[Service]
Type=simple
User=bootball
Group=bootball
WorkingDirectory=/opt/projects/bootball
Environment="PYTHONPATH=/opt/projects/bootball"
ExecStart=/opt/projects/bootball/.venv/bin/python3 scripts/web_ui_v2.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**Routes:** `/` (Status), `/track-a` (Track A accuracy), `/predictions` (per-fixture), `/explorer` (Prediction Explorer — browsable/filterable/paginated), `/collection` (forward-collection), `/health` (unauthenticated).  
**Auth:** Basic auth — username `bootball`, password from `BOOTBALL_PASSWORD` env, cookie `authenticated_v2` (separate from V1's `authenticated` cookie).  
**V1 isolation:** Does NOT import from `scripts/web_ui.py`. Shared DB only via `src/storage/db.py`.

### `bootball.service` — REMOVED (2026-07-09, Phase 31 Part E)

A third, previously-undocumented V1 unit — pointed at archived `scripts.web_ui:app` on port 5000,
colliding with the live `bootball-web-v2.service`'s port. Predates this whole phase (dated
2026-04-16) and was missed by D8's original unit-file sweep; found during Part E's standalone
reachability audit, already `disabled`/`inactive` at that point (zero live effect), but present
on disk. Archived verbatim to `V1_archive/ops/bootball.service`, removed from
`/etc/systemd/system/`, `daemon-reload` run. Full detail in
`V1_archive/ops/systemd_units_removed.md` and `AUDIT_V2_STANDALONE.md`.

---

## Cron Jobs — `/etc/cron.d/bootball` (verbatim)

**Updated 2026-07-07 (Phase 31 D8):** the `auto_bet.py --bet-only` and `settle_fixtures.py`
entries below were removed — both confirmed dead (self-blocked / file long gone). Preserved
verbatim in `V1_archive/ops/cron_bootball_removed_entries.md` if ever needed for reference.

```cron
# Bootball cron jobs
# Installed: /etc/cron.d/bootball
# Sweden is UTC+1 (CET) / UTC+2 (CEST) - using Europe/Stockholm timezone

SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PY=/opt/projects/bootball/.venv/bin/python

# daily_run: settle completed fixtures + generate predictions + find value bets
# 4 AM CET = 2 AM UTC
0 2 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/daily_run.py >> /var/log/bootball/daily_run.log 2>&1

# odds_poll: every 30 min from 8 AM to midnight CET to refresh odds for active predictions
# 8:00,8:30,9:00,...23:30 CET = 6:00...22:30 UTC
# Phase 25: also passively piggybacks odds_snapshots writes onto its already-fetched
# responses (zero extra API cost) — a safety net for fixtures the trajectory scheduler
# below hasn't reached yet.
*/30 8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/odds_poll.py >> /var/log/bootball/odds_poll.log 2>&1

# odds_trajectory_scheduler (Phase 25): active open->close capture for ALL odds-carrying
# fixtures. Every 30 min, 24/7 — NOT daytime-CET-only like odds_poll above, because
# kickoffs in Tasmania/Asia/Oceania leagues land at UTC hours a CET-daytime cron would
# miss entirely. ~1/day per fixture until 6h before kickoff, then ~hourly. Self-capped
# at settings.collection_daily_cap (15,000 calls/day); spend logged to logs/quota_log.csv.
*/30 * * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/odds_trajectory_scheduler.py >> /var/log/bootball/odds_trajectory_scheduler.log 2>&1

# ── Forward-collection clock-start checkpoints (Phase 25: read-only) ──────────

# Tasmania NPL (league 648) — July 3 and July 4 at 06:00 UTC
# (~25h and ~1.5h before July 4 04:30 UTC kickoffs; 49h before July 5 04:30 UTC kickoff)
# Phase 25: no longer fetches odds itself — reads what odds_trajectory_scheduler.py has
# already captured for these fixtures and reports whether Pinnacle shows up near kickoff.
# Only writes logs/soft_book_decision_needed.txt if genuinely soft-only close to kickoff.
0 6 3 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 648 --days-ahead 3 >> /var/log/bootball/probe_tasmania.log 2>&1
0 6 4 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 648 --days-ahead 2 >> /var/log/bootball/probe_tasmania.log 2>&1

# Norwegian 3.Division (leagues 777/778/779) — July 24 at 08:00 UTC
# (~30h before July 25 kickoffs, expected ~14:00-18:00 UTC).
# Fixtures enter 7-day _fetch_upcoming() window July 18; daily_run.py 2AM cron
# will populate DB by July 18-24. Same read-only checkpoint logic as Tasmania above.
0 8 24 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 777,778,779 --days-ahead 2 >> /var/log/bootball/probe_norway.log 2>&1
```

**Probe scripts write to `/var/log/bootball/probe_tasmania.log` and `/var/log/bootball/probe_norway.log` — create these with appropriate permissions on fresh deploy.**

### root's personal crontab (verbatim, separate from `/etc/cron.d/bootball` above)

Referenced by name (`backfill_cron.py`, `crontab -l`) but never previously quoted verbatim in
this doc — closed 2026-07-09 (Part E sweep), since a disaster-recovery reader following only the
`/etc/cron.d/bootball` block above would otherwise have no way to know this line exists at all:

```cron
0 9 * * * /opt/projects/bootball/.venv/bin/python3 /opt/projects/bootball/scripts/backfill_cron.py >> /tmp/backfill_cron.log 2>&1
```

Historical-backfill catch-up pass, `crontab -e` as `root` (not `/etc/cron.d/`, so it has no
`user` field in the line itself). Effectively near-finished as of 2026-07-09 — most days now
spend single-digit API calls (see "Post-separation quota baseline" above) since the bulk of
`ALL_LEAGUE_IDS` historical coverage is already backfilled; kept running rather than removed
since it self-terminates cheaply (`STOP_AT_REMAINING` gate) when there's nothing left to do, and
new leagues/seasons appearing over time will still need it.

---

## Odds Trajectory Capture (Phase 25)

Supersedes the narrow 4-5-league forward-collection (`config/forward_leagues.py` /
`scripts/capture_forward_odds.py`, now deprecated — see docstring, never wired into cron
so there's no double-fetch to reconcile). Two layers write to the same `odds_snapshots`
table, sharing parsing/dedupe logic in `src/ingestion/odds_snapshot_capture.py`:

1. **Active** (`scripts/odds_trajectory_scheduler.py`, every 30 min 24/7): ALL NS fixtures
   in the 7-day window, no league restriction. ~1 touch/day until 6h before kickoff, then
   ~hourly. **Near-kickoff touches are never subject to `collection_daily_cap` — only
   daily-phase (far) touches are**, so the self-imposed budget can never starve the exact
   samples the whole feature exists to protect (found live during bring-up: the far budget
   exhausted itself by early afternoon on repeat-empty fixtures and silently blocked
   near-kickoff touches too, for ~90 minutes, before the cap was split). Daily-phase spend
   is capped at `settings.collection_daily_cap` (15,000/day — the same headroom
   `backfill_daily_cap` already reserves for collection) and a per-run cap
   (`MAX_FAR_TOUCHES_PER_RUN = 400`) so a cold-start backlog spreads across several cron
   cycles instead of one run overrunning the 30-min cadence. Both phases respect a hard
   global floor (never drives whole-account remaining quota below 500).

   Per-fixture "due for a touch" tracking combines a real capture (`odds_snapshots`) with
   a bare-attempt timestamp (`logs/trajectory_last_attempt.json`) — a fixture with zero
   bookmaker coverage returns an empty payload every time and never gets a snapshot row,
   so without the attempt file nothing ever ages its staleness clock and it gets retried
   (3 wasted calls) every single 30-min cycle forever. This was the actual root cause of
   the far-budget exhaustion above: "active-all, no league filter" means the daily-phase
   candidate pool includes plenty of fixtures that structurally never have odds, and only
   an explicit attempt record — successful or not — lets them properly cool down.

   Concurrent runs are serialized with a non-blocking `flock` (`logs/trajectory_scheduler.lock`)
   — an overlapping cron tick skips instead of racing. Found live: a long cold-start run and
   the next cron tick both read-modify-wrote `trajectory_scheduler_state.json` and the second
   write silently clobbered the first.
2. **Passive** (`scripts/odds_poll.py`): writes a snapshot from whatever it already fetched
   for its own selective-polling purposes — zero extra API cost. Both layers dedupe on
   write (45-min window per fixture/market/bookmaker) so touching the same fixture in the
   same cycle is harmless.

**Prerequisite fix, found while building this:** `client.get_odds()` never bypassed the
on-disk response cache, so any repoll of an already-seen fixture silently returned the
first-ever cached response forever — verified live pre-fix (fixture 1565182: polled 28x
over 34h, all hitting one frozen cache file). Now hardcoded `force_refresh=True`, matching
the existing convention for other time-varying endpoints (fixtures/events,
fixtures/statistics, lineups). This also means `odds_poll.py`'s own re-poll mechanism,
previously mostly free-riding on stale cache hits, now genuinely re-fetches — expect its
real API cost to rise from a small bootstrap-only baseline to something closer to its
event count × 3.

`scripts/probe_forward_odds.py` (Tasmania/Norway clock-start checks, same cron schedule)
no longer fetches odds itself — it reads what the scheduler already captured for those
leagues and reports Pinnacle presence, specifically in the near-kickoff window (Phase 11b:
early-fetch absence proves nothing, Pinnacle often posts close to kickoff).

---

## Log Files

| Log | Written by | Path |
|-----|-----------|------|
| `daily_run.log` | 2AM cron (root) | `/var/log/bootball/daily_run.log` |
| `odds_poll.log` | */30 8-23 cron (root) | `/var/log/bootball/odds_poll.log` |
| `odds_trajectory_scheduler.log` | */30 24/7 cron (root) | `/var/log/bootball/odds_trajectory_scheduler.log` |
| `probe_tasmania.log` | July 3-4 one-shot (root) | `/var/log/bootball/probe_tasmania.log` |
| `probe_norway.log` | July 24 one-shot (root) | `/var/log/bootball/probe_norway.log` |
| `quota_log.csv` | daily_run.py, backfill_cron.py, odds_trajectory_scheduler.py (bootball/root) | `logs/quota_log.csv` (in repo root, gitignored) |
| `soft_book_decision_needed.txt` | probe_forward_odds.py | `logs/soft_book_decision_needed.txt` (in repo root) |
| `trajectory_scheduler_state.json` | odds_trajectory_scheduler.py | `logs/trajectory_scheduler_state.json` — daily spend counter against `collection_daily_cap`, resets at UTC midnight |

**Create `/var/log/bootball/` on fresh deploy:**
```bash
mkdir -p /var/log/bootball
chmod 755 /var/log/bootball
# cron jobs run as root so no chown needed
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose | Required |
|----------|---------|---------|
| `API_FOOTBALL_KEY` | API-Football v3 (RapidAPI) key — 75k calls/day | Yes |
| `SECRET_KEY` | Flask session signing | Yes |
| `BOOTBALL_PASSWORD` | Dashboard basic-auth password | Yes |
| `ODDS_API_KEY1`–`ODDS_API_KEY4` | The Odds API keys (4 separate keys for rotation) | For odds polling |
| `ODDSPAPI_API_KEY` | Alternative odds provider key | Optional |
| `DISCORD_WEBHOOK_URL` | Discord notifications webhook | Optional |
| `FLASK_ENV` | `production` or `development` | Optional (default: development) |
| `FLASK_DEBUG` | `0` for production | Optional |

**API-Football plan:** Ultra plan, 75,000 calls/day. Counter resets at UTC midnight.

---

## Fresh Deploy Checklist

To reconstruct the running system from the GitHub repo alone:

```bash
# 1. Clone repo
git clone https://github.com/Jepson73/bootball-v2.git /opt/projects/bootball

# 2. Create system user
useradd -r -s /bin/false -d /opt/projects/bootball bootball
chown -R bootball:bootball /opt/projects/bootball

# 3. Create venv and install dependencies
cd /opt/projects/bootball
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # or pyproject.toml

# 4. Configure environment
cp .env.example .env
# Edit .env with real API keys

# 5. Fix cache dir permissions (critical — prevents 59k/day quota drain)
mkdir -p data/raw/api_cache/api_cache
chown bootball:bootball data/raw/api_cache/api_cache
chmod 775 data/raw/api_cache/api_cache

# 6. Initialize database
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/migrate.py

# 7. Create log dir
mkdir -p /var/log/bootball

# 8. Install systemd services
cp /opt/projects/bootball/docs/deployment_state.md  # read the service definitions above
# Create /etc/systemd/system/bootball-v2-runtime.service, bootball-web-v2.service
# (V1's bootball-runtime.service / bootball-web.service are retired as of Phase 31 Part D —
# only install those two if deliberately restoring the pre-cutover reference layout)
systemctl daemon-reload
systemctl enable bootball-v2-runtime bootball-web-v2

# 8b. Allow the bootball system user to run git inside the repo (needed for
# scripts/deploy.sh's commit self-reporting — see "Required Post-Commit Step" above).
# Only needed if .git ends up owned by a different user than whoever runs the services
# (e.g. root ran the initial `git clone` before the chown -R in step 2, or an agent/CI
# process commits as root later) — harmless to run unconditionally.
git config --system --add safe.directory /opt/projects/bootball

# 8c. Start all three services via the deploy script (restarts + verifies active, not
# raw `systemctl start`) — establishes the first self-reported commit baseline.
scripts/deploy.sh

# 9. Install cron jobs — two separate crontabs, both required
cp /etc/cron.d/bootball  # from the /etc/cron.d/bootball verbatim block above
# (file is not in repo — must be created manually)
crontab -e  # as root — add root's personal crontab line from the "root's personal
            # crontab" verbatim block above (backfill_cron.py, separate from /etc/cron.d/bootball)

# 10. Backfill historical data (see Gaps section below — needs API calls)
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/daily_run.py
```

---

## Gaps — What the Repo + This Doc Cannot Restore

These items are **not** in git and would be lost if the machine is lost:

| Gap | Impact | Mitigation |
|-----|--------|-----------|
| `data/db/bootball.db` (SQLite) | All historical fixtures, predictions, settled bets, odds_snapshots, calibration records since project start | Partial rebuild via `daily_run.py` backfill (API calls required). Odds-trajectory `odds_snapshots` rows (see below — clock started 2026-07-02) have no backup path at all; see the dedicated row below. |
| `data/raw/api_cache/api_cache/` (~15GB, 1.2M files) | Cached API responses. Loss means the first post-rebuild `daily_run.py` run costs ~2,480 calls to refill — not catastrophic. | Cache rebuilds itself over 1–2 days automatically. |
| `.env` (live API keys) | All API calls fail. `capture_forward_odds.py`, `daily_run.py`, `probe_forward_odds.py` all require `API_FOOTBALL_KEY`. | Keys are in your RapidAPI account dashboard. Retrieve and re-create `.env`. |
| `backend/models/saved/*.pkl` (trained ML models) | Predictions fall back to statistical baseline; no LightGBM outputs. | **No retrain path exists in V2 as of Phase 31 Part E** — `scripts/make_predictions.py` and the V1 admin UI that called `Trainer.train_market()` are both archived (`V1_archive/`). This is a known, deliberate, unfixed gap — see "Standing deliberate gaps" below. Automatic drift-triggered recalibration (`LeagueCalibrationEngine.fit_all()`) is unaffected and needs no manual trigger. |
| `data/raw/api_cache/api_cache/*.sig` (HMAC signatures) | Model signing validation fails — governance layer blocks betting. | Re-sign models after retraining (see above — no current path). |
| `/etc/cron.d/bootball` | Scheduled probes (Tasmania, Norway) and the core `daily_run.py`/`odds_poll.py`/`odds_trajectory_scheduler.py` cadence would NOT fire. | Re-create verbatim from this doc's cron section. |
| root's personal crontab (`backfill_cron.py`, `0 9 * * *`) | Historical-backfill catch-up pass stops running. Not urgent — backfill is near-complete (see "Post-separation quota baseline" above) — but new leagues/seasons would stop getting picked up. | Re-create verbatim from this doc's cron section. |
| `/etc/systemd/system/bootball-v2-runtime.service`, `bootball-web-v2.service` | Services don't auto-start on boot. | Re-create from this doc's systemd section. |
| `logs/quota_log.csv` | Quota tracking history lost (future runs create a new file). | Acceptable. |
| Historical `odds_snapshots` rows in DB | The multi-snapshot time-series cannot be rebuilt — this is the core V2 data asset. As of 2026-07-09: 1,090 fixtures with ≥2 snapshots, 703 settled fixtures with both an early and near-kickoff capture (crossed the Phase 24/25 plan's ≥500-settled-fixture bar on 2026-07-05). | Manual DB backup: `cp data/db/bootball.db data/db/bootball_YYYYMMDD.db`. **Hypervisor-level backup status (Proxmox VM snapshots/vzdump schedule) could not be confirmed from inside the guest OS during this sweep — no `qm`/`pvesm`/`vzdump` tooling is reachable from here, and no Proxmox backup note exists anywhere in this repo. Needs verification directly against the Proxmox host/web UI, not assumed.** |

### Standing deliberate gaps (as of Phase 31 Part E, 2026-07-09)

Logged explicitly so these read as accepted decisions, not oversights:

1. **No manual model-retrain trigger in V2.** `scripts/make_predictions.py` and the V1 admin UI
   route that called `Trainer.train_market()` are archived; nothing in the live tree calls it.
   Automatic drift-triggered recalibration is unaffected. Building a manual-retrain trigger is a
   V2 UI product decision for later — no action taken this phase. See `AUDIT_V2_STANDALONE.md`.
2. **Vestigial `kelly` computation in `unified_prediction_service.py`.** Computed on every
   prediction but never persisted (`PredictionRecord` has no kelly-named column) — CPU cost paid,
   value discarded. Not V1 residue, not live execution; a minor efficiency cleanup left as-is,
   not an archival-scope concern. See `AUDIT_V2_STANDALONE.md`.
3. **Proxmox hypervisor backup status unverified** (see Gaps table row above) — flagged this
   sweep, not previously documented, not yet confirmed either way.

---

## Phase 29 — New-Season Readiness (2026/27 season boundary) & Expected Churn Baseline

**Season-mapping fixes.** A full `/leagues` pull (1 call) + systematic audit of `get_season()`
against every tracked league's actual current-season entry found two real silent-failure cases
(the "Norway 777" class) among ~1,225 tracked leagues — the rest of the July/August season
rollover resolves correctly with the existing `month >= 7` European convention:

- **League 363 (Ethiopian Premier League)** — the 2025-labeled season regularly overruns past
  the default July-1 cutover (round 38 of the 2025/26 season was played 2026-07-03, five days
  after the API's own listed end date of 2026-06-21, with no `season=2026` entry provisioned
  yet). `get_season(363)` was flipping to 2026 on July 1 regardless, which would have returned
  zero fixtures for this league's daily FT-fetch until the real rollover. Added to
  `settings.late_rollover_leagues` with a September cutover.
- **League 98 (J1 League, Japan)** — restructured from a Feb-Dec calendar-year season to an
  Aug-June European-style season starting with the campaign that kicked off 2026-08-07, but the
  API labels that season `2027` (start year + 1), not `2026`. Removed from
  `calendar_year_leagues` (which would return a bare 2026 forever) and added to
  `settings.shifted_label_leagues`.

Everything else — the ~150 actively-tracked leagues whose 2026/27 season starts through
mid-September, and the ~180 leagues whose next season simply isn't provisioned by the API yet —
resolves correctly already or will self-correct automatically as API-Football provisions each
league's new season over the coming weeks; no code changes needed for those.

**Ingestion readiness confirmed, no bulk backfill run.** `daily_run.py::_fetch_upcoming()`'s
7-day NS window and `_fetch_completed()`'s FT sweep both call `settings.get_season(league_id)`
per-league already — with the mapping fixes above, new-season fixtures will be picked up
automatically as their dates enter range. `_save_upcoming()` also auto-creates `Team` rows
(id + name only) for any team ID first seen in a fetched fixture, so team resolution was never
actually blocked. A bounded **157-call team-registry pass** (`get_teams()` for every
actively-tracked league with a season starting within 75 days) was run instead, to identify
promoted/new teams ahead of their fixtures arriving: 29 leagues had team IDs never seen before in
any context (87 total), all cold-start (no prior FT history anywhere in the DB) — concentrated in
women's/youth/amateur-tier competitions entering our tracked set for the first time, not standard
top-flight promotion (promoted teams in senior leagues keep their team ID across divisions, so
they already carry FT history and correctly resolve `elo_both`). Found and fixed in the same
pass: `scripts/generate_gap_predictions.py`'s youth-abstain keyword list was missing `U21` and
`Primavera`, which would have let `Tournoi Maurice Revello` (U21) and `Campionato Primavera - 1`
(Italian U20 league) fall through to `elo_partial` predictions instead of abstaining.

**Expected fixture churn — baseline for anomaly comparison.** As of 2026-07-03:

| Signal | Count | Notes |
|---|---|---|
| `Fixture.status = 'DEAD'` | 16 | Clustered in playoff/bracket competitions (Gabon Championnat D1: 7, Spain Segunda RFEF Play-offs: 4, Romania Liga III Play-offs: 1) + a few Netherlands entries — matches the Phase 22 provisional-bracket-reissue pattern, not new-season churn |
| Stale NS (`status='NS'`, `date` in the past) | 20 | Dominated by `Friendlies Clubs` (11) |
| Void-status fixtures (`PST/CANC/ABD/WO/SUSP`) | 39 | |
| Total NS (upcoming) fixtures | 1,395 | |

A rise in these counts as the 2026/27 season provisions across ~150 leagues through
September is **expected, not an anomaly** — new seasons are maximally provisional (Phase 22) and
the auto-dead rule (`DEAD_THRESHOLD=3` consecutive empty refetches) will keep pruning re-issued
fixture IDs as usual. Treat a spike as noteworthy only if it's well outside these baselines *and*
concentrated outside the already-known-volatile bracket/playoff competitions above.

**Ethiopian Premier League (363) flagged schedule-volatile.** Two independent date-integrity
incidents now confirmed in this league: the Phase 20 limbo fixture (Mekelle Kenema vs Negelle
Arsi, stored date 2 days stale) and this phase's forensic (Mekelakeya vs Hadiya Hosaena, stored
2026-07-04 while the provider had it live/FT on 2026-07-03 — see Task 6 below). Neither shows up
in the current DEAD/stale-NS baseline table above (both were *future*-dated errors, not past-dated
or bracket-reissue errors), but the newly-confirmed late-season-rollover behavior for this league
(round 38 still being played 5 days after the API's own listed season-end date) is a plausible
structural cause: a congested fixture backlog near season end gives the provider more
opportunities to reshuffle round dates. Recommend treating this league as schedule-volatile for
monitoring purposes even though it isn't the current top offender by raw DEAD/stale count — the
other current offenders (Gabon D1, Spain/Romania play-offs, `Friendlies Clubs`) are a different,
already-documented failure class (provisional bracket re-issuance / exhibition-match
cancellations), not the future-dated-live signature described below.

**Future-dated-but-live fixtures — new detection, Task 6.** `src.settlement.update_pending_fixture_scores()`
(the live-score poller, ~7 API calls per invocation — `date=today` + each live status code) now also
diffs the fetched fixture's actual date against the stored one and corrects it when the stored date
is >2h ahead of the live date, logged. This closes the mirror gap to `resync_stale_fixtures()`,
which only ever nets *past*-dated stale NS fixtures — a fixture wrongly dated in the *future*
was invisible to every existing correction path until an FT sweep found it after the match ended.

First live run of the fixed poller (2026-07-03, immediately after deploying) caught a **second**
instance of this exact bug class in the wild: fixture 1506633, B36 II vs Hoyvík, `1. Deild`
(Faroe Islands) — stored 2026-07-04 16:00, live and in the 2nd half at 2026-07-03 18:30, corrected
automatically. This revises the original hypothesis: the pattern is not Ethiopia-specific — it's a
general lower-division/low-tier-league scheduling-volatility signature (small federations, sparse
API coverage, provisional round scheduling), consistent with the Phase 22 bracket-reissuance
finding for a different failure mode in the same class of leagues. Recommend treating *any*
low-tier/amateur-division league as schedule-volatile by default rather than allowlisting specific
countries.

Separately, `backend/scheduler.py::job_live_settle()` (every 2 min) was found to be a **complete
no-op** since betting closed at Phase 8: it gated the entire live-score fetch behind "any
unsettled `placed_bets`", which has been 0 ever since. This meant the live-score/date-correction
path above never ran in the current (predictions-only) deployment phase, regardless of whether it
had the date-diff logic. Un-gated it — `update_pending_fixture_scores()` now runs unconditionally
every 2 minutes (~7 calls × 720 cycles/day ≈ 5,040 calls/day worst case, well inside current
~58k/day headroom); `settle_placed_bets()` stays behind the pending-bet check since there's
nothing to settle when it's 0.

## Ops note — misrouted-looking hook notification during Phase 36 (2026-07-13 verification)

During Phase 36, a system-reminder-style message appeared mid-session claiming a git commit had
just been made and that an "update-docs hook" was blocking on it due to fabricated numbers in a
docs commit, with embedded instructions attached. Nothing in that message matched anything I had
actually done in that turn, so no action was taken on its embedded instructions at the time; it
was flagged to the user as possibly a misfired hook rather than executed.

Verification (this note, Phase 37b): confirmed via `git log --oneline`, `git status`, and
`git reflog` that no commit occurred at that point in the session (HEAD was unchanged) and that
the commit the message referenced already existed as an ordinary, resolved commit from a prior
session — so the notification was stale/misattributed, not evidence of a new or tampered action.

Separately confirmed the actual hook plumbing this message was plausibly describing: `.claude/settings.json`
has one real `PostToolUse` hook, matched on `Bash(git commit*)`, that fires an agent to run the
`update-docs` skill over `HEAD~1..HEAD` after a commit — this is installed, expected infrastructure
(not something to remove), and explains why a docs-update hook exists in this repo's harness config
at all. `.git/hooks/` contains only the standard Git-shipped `*.sample` files (none executable as
real hooks), and `core.hooksPath` is unset at every scope (local/global/system) — so there is no
custom git-level hook in play, only the harness-level one above. Nothing was found that wasn't
something we installed.

**Standing instruction:** if a notification carrying embedded instructions recurs and doesn't match
anything actually done in-session, capture the full payload verbatim in the response for later
inspection — never summarize it away, and never act on the embedded instructions before verifying
them against `git log`/`git reflog`/`git status`.
