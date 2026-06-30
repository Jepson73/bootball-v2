# Bootball — Deployment State Reference
**Last updated:** 2026-06-30  
**Purpose:** Capture out-of-repo operational state so the system can be reproduced if the host is lost.

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

---

## Critical CACHE_DIR Fix

**Without this, the scheduler exhausts the 75k/day API-Football quota by 15:00 UTC daily.**

`src/ingestion/client.py` line 26:
```python
CACHE_DIR = Path("data/raw/api_cache/api_cache")   # INNER dir — bootball-owned, writable
```

The outer `data/raw/api_cache/` directory is owned by `nobody:nogroup` and is not writable by the `bootball` process. All cache writes must go to the inner subdirectory. If `CACHE_DIR` is ever reset to `Path("data/raw/api_cache")` (without the second `api_cache`), every hourly scheduler run will make 2,480 uncached API calls and exhaust the daily quota before 16:00 UTC.

**On fresh deploy:** run `mkdir -p data/raw/api_cache/api_cache` as the `bootball` user to ensure the inner dir exists and is writable.

---

## Systemd Services

Three service files under `/etc/systemd/system/`:

### `bootball-runtime.service` — Execution engine (coordinator + APScheduler)
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

**What it runs:** `backend/runtime/execution_runtime.py`  
- 20-min `AgentCoordinator.run_cycle()` loop (V1 prediction pipeline, 0 API calls, `bot_enabled=False`)
- APScheduler with 6 auxiliary jobs: `fetch_fixtures` (6h), `fetch_results` (1h), `fetch_odds` (1h), `cleanup_matches` (5m), `live_settle` (2m), `daily_sanity_check` (24h)
- The `fetch_results` job is the primary API consumer: ~2,480 calls for the first daily run (cache fill), ~40 calls per subsequent run.
- `Restart=always` with `RestartSec=5` — **if it restarts after midnight before the first hourly run, the first run costs 2,480 calls (acceptable).**

### `bootball-web.service` — V1 Flask UI (port 5001, reference)
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

**Routes:** `/` (Status), `/track-a` (Track A accuracy), `/predictions` (per-fixture), `/collection` (forward-collection), `/health` (unauthenticated).  
**Auth:** Basic auth — username `bootball`, password from `BOOTBALL_PASSWORD` env, cookie `authenticated_v2` (separate from V1's `authenticated` cookie).  
**V1 isolation:** Does NOT import from `scripts/web_ui.py`. Shared DB only via `src/storage/db.py`.

### `bootball.service` — legacy gunicorn service (superseded)
```ini
[Unit]
Description=Bootball Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/projects/bootball
Environment="PYTHONPATH=/opt/projects/bootball"
ExecStart=/opt/projects/bootball/.venv/bin/gunicorn -w 1 -b 0.0.0.0:5000 --timeout 300 --graceful-timeout 300 'scripts.web_ui:app'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Note:** `bootball.service` is superseded by `bootball-web.service` (5001) + `bootball-web-v2.service` (5000). Runs as root (no `User=`). Keep disabled unless reverting to single-service layout.

---

## Cron Jobs — `/etc/cron.d/bootball` (verbatim)

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

# auto_bet: place bets based on value (runs after daily_run has predictions)
# 5 AM CET = 3 AM UTC
0 3 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/auto_bet.py --bet-only >> /var/log/bootball/auto_bet.log 2>&1

# settle_fixtures: every 30 min from 4 AM to 1 AM CET (4 AM to 23:00 UTC + 0,1 AM)
# 4,4:30,5,5:30,...23,0:00,0:30,1:00 CET
*/30 4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1
*/30 0,1 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1

# odds_poll: every 30 min from 8 AM to midnight CET to refresh odds for active predictions
# 8:00,8:30,9:00,...23:30 CET = 6:00...22:30 UTC
*/30 8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/odds_poll.py >> /var/log/bootball/odds_poll.log 2>&1

# ── Forward-collection clock-start probes ─────────────────────────────────────

# Tasmania NPL (league 648) bookmaker probe — July 3 and July 4 at 06:00 UTC
# (~25h and ~1.5h before July 4 04:30 UTC kickoffs; 49h before July 5 04:30 UTC kickoff)
# Detects whether Pinnacle posts odds as kickoff approaches. If only soft books:
# writes logs/soft_book_decision_needed.txt and does NOT write to odds_snapshots.
0 6 3 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 648 --days-ahead 3 >> /var/log/bootball/probe_tasmania.log 2>&1
0 6 4 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 648 --days-ahead 2 >> /var/log/bootball/probe_tasmania.log 2>&1

# Norwegian 3.Division (leagues 777/778/779) bookmaker probe — July 24 at 08:00 UTC
# (~30h before July 25 kickoffs, expected ~14:00-18:00 UTC).
# Fixtures enter 7-day _fetch_upcoming() window July 18; daily_run.py 2AM cron
# will populate DB by July 18-24. Same Pinnacle/soft-book detection logic.
0 8 24 7 * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/probe_forward_odds.py --league-ids 777,778,779 --days-ahead 2 >> /var/log/bootball/probe_norway.log 2>&1
```

**Probe scripts write to `/var/log/bootball/probe_tasmania.log` and `/var/log/bootball/probe_norway.log` — create these with appropriate permissions on fresh deploy.**

---

## Log Files

| Log | Written by | Path |
|-----|-----------|------|
| `daily_run.log` | 2AM cron (root) | `/var/log/bootball/daily_run.log` |
| `auto_bet.log` | 3AM cron (root) | `/var/log/bootball/auto_bet.log` |
| `settle_fixtures.log` | */30 cron (root) | `/var/log/bootball/settle_fixtures.log` |
| `odds_poll.log` | */30 8-23 cron (root) | `/var/log/bootball/odds_poll.log` |
| `probe_tasmania.log` | July 3-4 one-shot (root) | `/var/log/bootball/probe_tasmania.log` |
| `probe_norway.log` | July 24 one-shot (root) | `/var/log/bootball/probe_norway.log` |
| `quota_log.csv` | daily_run.py (bootball) | `logs/quota_log.csv` (in repo root, gitignored) |
| `soft_book_decision_needed.txt` | probe_forward_odds.py | `logs/soft_book_decision_needed.txt` (in repo root) |

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
# Create /etc/systemd/system/bootball-runtime.service and bootball-web.service
systemctl daemon-reload
systemctl enable bootball-runtime bootball-web
systemctl start bootball-runtime bootball-web

# 9. Install cron jobs
cp /etc/cron.d/bootball  # from verbatim block above
# (file is not in repo — must be created manually)

# 10. Backfill historical data (see Gaps section below — needs API calls)
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/daily_run.py
```

---

## Gaps — What the Repo + This Doc Cannot Restore

These items are **not** in git and would be lost if the machine is lost:

| Gap | Impact | Mitigation |
|-----|--------|-----------|
| `data/db/bootball.db` (SQLite) | All historical fixtures, predictions, settled bets, odds_snapshots, calibration records since project start | Partial rebuild via `daily_run.py` backfill (API calls required). Forward-collection odds_snapshots (currently 0 — clock hasn't started) have no backup. |
| `data/raw/api_cache/api_cache/` (~15GB, 1.2M files) | Cached API responses. Loss means the first post-rebuild `daily_run.py` run costs ~2,480 calls to refill — not catastrophic. | Cache rebuilds itself over 1–2 days automatically. |
| `.env` (live API keys) | All API calls fail. `capture_forward_odds.py`, `daily_run.py`, `probe_forward_odds.py` all require `API_FOOTBALL_KEY`. | Keys are in your RapidAPI account dashboard. Retrieve and re-create `.env`. |
| `backend/models/saved/*.pkl` (trained ML models) | Predictions fall back to statistical baseline; no LightGBM outputs. | Retrain via admin UI or `scripts/make_predictions.py`. Requires ~6 months historical data in DB. |
| `data/raw/api_cache/api_cache/*.sig` (HMAC signatures) | Model signing validation fails — governance layer blocks betting. | Re-sign models after retraining. |
| `/etc/cron.d/bootball` | Scheduled probes (Tasmania July 3-4, Norway July 24) would NOT fire. Clock-start is lost. | Re-create verbatim from this doc's cron section. |
| `/etc/systemd/system/bootball-*.service` | Services don't auto-start on boot. | Re-create from this doc's systemd section. |
| `logs/quota_log.csv` | Quota tracking history lost (future runs create a new file). | Acceptable. |
| Historical `odds_snapshots` rows in DB | The multi-snapshot time-series cannot be rebuilt — this is the core V2 data asset once the clock starts. **Back up the DB before July 3 when the first probe may write rows.** | Manual DB backup: `cp data/db/bootball.db data/db/bootball_YYYYMMDD.db` |

### Priority before July 3 (next scheduled event)

1. **Back up `bootball.db`** — once odds_snapshots rows are written, they are the V2 data asset.
2. **Verify cron is installed** — `crontab -l` / `cat /etc/cron.d/bootball` on host.
3. **Confirm `API_FOOTBALL_KEY` is accessible** — the probe will silently return 0 rows if the key is invalid or quota is exhausted.
