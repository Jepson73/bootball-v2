# Bootball Cron Schedule

## Actual Cron File
`/etc/cron.d/bootball` - installed system cron (not user crontab)

## Schedule (CET = UTC+2)

| Time (CET) | Time (UTC) | Script | Purpose |
|------------|------------|--------|---------|
| 4:00 | 2:00 | `daily_run.py` | Fetch FT fixtures, settle bets, generate predictions, find value bets |
| 5:00 | 3:00 | `auto_bet.py --bet-only` | Place bets automatically based on value |
| Every 30 min, 4 AM - 3 AM | Every 30 min, 2 AM - 1 AM | `settle_fixtures.py` | Settle placed bets |

## Current Crontab Contents

```cron
# Bootball cron jobs
# Sweden is UTC+2 (CET)

SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PY=/opt/projects/bootball/.venv/bin/python

# daily_run: settle completed fixtures + generate predictions + find value bets
# 4 AM CET = 2 AM UTC
0 2 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/daily_run.py >> /var/log/bootball/daily_run.log 2>&1

# auto_bet: place bets based on value (runs after daily_run has predictions)
# 5 AM CET = 3 AM UTC
0 3 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/auto_bet.py --bet-only >> /var/log/bootball/auto_bet.log 2>&1

# settle_fixtures: every 30 min from 4 AM to 3 AM CET (4 AM to 1 AM UTC)
*/30 4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1
*/30 0,1 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1
```

## API Usage

- settle_fixtures runs ~46 times/day (every 30 min)
- Each run: ~2 API calls (fetch + settle)
- Daily API usage for settling: ~92 calls/day (~0.1% of 75,000 budget)

## Manual Runs

```bash
# daily_run manually (with lock check)
cd /opt/projects/bootball
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/daily_run.py

# auto_bet manually
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/auto_bet.py --bet-only

# settle_fixtures manually
PYTHONPATH=/opt/projects/bootball .venv/bin/python scripts/settle_fixtures.py

# Or via web UI admin panel
curl -X POST http://localhost:5000/api/admin/daily_run -H "Cookie: ..."
curl -X POST http://localhost:5000/api/admin/place_bets -H "Cookie: ..."
curl -X POST http://localhost:5000/api/admin/settle -H "Cookie: ..."
```

## Lock File

Manual daily_run from web UI checks for `/tmp/bootball_daily_run.lock`. If another daily_run is in progress, it will refuse to start and return a 409 error.

## Discord Alerts

- `daily_run.py` - sends completion alert with summary
- `auto_bet.py` - sends formatted alert with all placed bets
- `settle_fixtures.py` - sends formatted alert with settled bet results (W/L/P/L)

## Log Files

- `/var/log/bootball/daily_run.log`
- `/var/log/bootball/auto_bet.log`
- `/var/log/bootball/settle_fixtures.log`
- `/var/log/bootball/maintenance.log`
