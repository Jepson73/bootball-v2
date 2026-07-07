# Removed /etc/cron.d/bootball entries — Phase 31 Part D (D8)

Preserved verbatim rather than deleted, per the "move not delete" principle applied to
code elsewhere in this archive. Both entries were confirmed dead before removal (see
`OWNERSHIP.md`'s cron-surface findings table) — this file is the record of what ran and
why it was removed, not a restoration script.

## `scripts/auto_bet.py --bet-only` (daily, self-blocked)

```cron
# auto_bet: place bets based on value (runs after daily_run has predictions)
# 5 AM CET = 3 AM UTC
0 3 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/auto_bet.py --bet-only >> /var/log/bootball/auto_bet.log 2>&1
```

**Why removed:** ran every day at 03:00 UTC, hit its own `check_legacy_execution_allowed()`
guard (independent of `bot_enabled`), raised `RuntimeError: LEGACY EXECUTION BLOCKED`, and
wrote nothing — confirmed via log tail on 2026-07-05. `scripts/auto_bet.py` itself archived
to `V1_archive/scripts/auto_bet.py` in Part D7b.

## `scripts/settle_fixtures.py` (every 30 min, most hours)

```cron
# settle_fixtures: every 30 min from 4 AM to 1 AM CET (4 AM to 23:00 UTC + 0,1 AM)
# 4,4:30,5,5:30,...23,0:00,0:30,1:00 CET
*/30 4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1
*/30 0,1 * * * root cd /opt/projects/bootball && PYTHONPATH=/opt/projects/bootball $PY scripts/settle_fixtures.py >> /var/log/bootball/settle_fixtures.log 2>&1
```

**Why removed:** `scripts/settle_fixtures.py` was deleted from the repo in the 2026-05-25
"Full codebase refresh" commit — every invocation since then (6+ weeks, every 30 minutes,
most hours of the day) has been erroring `can't open file ... No such file or directory` in
`/var/log/bootball/settle_fixtures.log`. Pure noise, zero effect; not a V1/V2 question, just
stale infrastructure nobody removed when the file went.
