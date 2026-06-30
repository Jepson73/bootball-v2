# Phase 12b Findings — Quota Drain Investigation + Scheduled Clock-Start Checks
**Date:** 2026-06-30  
**Status:** Complete (read-only investigation + cron setup)

---

## Task 1 — Quota Drain Call Chain + V1/V2 Classification

### The 20-min execution_runtime.py cycle (ZERO API calls)

`execution_runtime.py → execute_cycle()` every 1,200s:

```
execute_cycle()
  └─ coordinator.run_cycle()          ← 0 API calls (pure DB reads + writes)
       ├─ DB query: 663 NS fixtures
       ├─ UnifiedPredictionService.generate_with_fixture_data()  ← DB
       ├─ Risk Manager / Portfolio Engine / Policy Engine        ← DB
       ├─ Adversary Agent                                        ← DB
       ├─ CalibrationEngine / MetaPolicyEngine                   ← DB
       └─ bet persistence (gated: bot_enabled=False)             ← DB
  └─ _run_settlement()                ← 0 API calls normally
       ├─ run_maintenance()           ← API only if FT fixtures have null goals
       ├─ settle_placed_bets()        ← DB only
       └─ settle_predictions()        ← DB only
```

**Per 20-min cycle: 0 API calls** (conditional: 1 call per 20 FT fixtures missing goals,
which is 0 during Norwegian summer break with no recent FT results in forward leagues).

**72 cycles/day × 0 = 0 API calls/day** from the coordinator loop.

### The drain: APScheduler inside execution_runtime.py

`execution_runtime.py._start_scheduler()` starts `backend.scheduler.start_scheduler()`, which
schedules six auxiliary jobs. The drain is entirely here:

| Job | Schedule | Calls/run (pre-fix) | Calls/run (post-fix cache hit) |
|-----|----------|---------------------|-------------------------------|
| `job_fetch_results` | every 1h (24×/day) | **2,480** (1,225 upcoming + 1,225 completed + ~30 standings) | **~40** (standings only; fixture data cache hits) |
| `job_fetch_fixtures` | every 6h (4×/day) | 2,480 + odds seed | ~40 + odds seed |
| `job_fetch_odds` | every 1h | 0 (no fixtures needed odds during summer break) | 0–30 (Tasmania fixtures now in window) |
| `job_live_settle` | every 2 min | 0 (no pending bets; bot_enabled=False) | 0 |
| `job_cleanup_matches` | every 5 min | 0 (DB-only) | 0 |
| `job_daily_sanity_check` | every 24h | DB-only | DB-only |

**Primary drain: `job_fetch_results` at 2,480 calls/hour = 59,520 calls/day** before CACHE_DIR fix.
This is why quota was exhausted daily at ~15:00-16:00 UTC.

**Root cause of pre-fix drain magnitude:** `DailyBaselinePipeline.run()` is called every hour
with no cache benefit because `CACHE_DIR` pointed at `data/raw/api_cache/` (nobody:nogroup),
so every cache write failed silently with EACCES, and every subsequent read was a miss.

### Why the fix works now (and didn't help before)

The CACHE_DIR fix (`Path("data/raw/api_cache/api_cache")`) was committed June 29 at 11:49 UTC.
PID 120 (execution_runtime.py) at the time had already imported `client.py` at 04:20 UTC — the
fix did not reach the running process.

**The process restarted at June 30 04:19 UTC** (confirmed via `ps -o lstart -p 120`). The new
process imports the fixed `client.py` and writes cache to the inner dir (bootball-owned,
drwxrwxrwx). First hourly run at 04:19 UTC fills the cache; all subsequent runs hit it.

### V1 / V2 Classification

**execution_runtime.py execute_cycle() → coordinator.run_cycle():**
- **Pure V1.** Generates betting predictions, allocates Kelly portfolio, runs governance pipeline
  (Risk, Policy, Adversary, CLVE), updates calibration. With `bot_enabled=False`, no bets are
  persisted — the system trains itself on stale predictions but places no capital at risk.
- V2 is a forward-looking EV measurement system. It requires fixture data + Pinnacle odds
  snapshots, not continuous prediction cycles. The coordinator cycle provides zero value to V2.

**APScheduler `job_fetch_results` / `job_fetch_fixtures`:**
- **Mixed: V2-relevant data maintenance, V1-hosting process.**
- The fixture/results fetch jobs maintain the DB that both V1 (predictions) and V2
  (capture_forward_odds.py reading fixtures from DB) depend on.
- However, both jobs are coupled to the execution_runtime.py process via `_start_scheduler()`.

### Recommendation

**Option A — Rate-limit `job_fetch_results` to every 6h (same as `job_fetch_fixtures`):**
- Changes scheduler.py line 570: `{'hours': 6}` instead of `{'hours': 1}`
- Requires execution_runtime.py restart to take effect
- Cuts scheduler's first-day baseline from 59,520 → 9,920 (pre-fix) or 960 → 160 (post-fix)
- Preserves the V2-relevant data maintenance; just reduces frequency
- Does NOT retire the V1 coordinator loop

**Option B — Decouple the scheduler from execution_runtime.py:**
- Extract `start_scheduler()` into its own service/process
- Allows killing the V1 coordinator loop (execution_runtime.py) without losing data maintenance
- Higher refactor cost; recommended once the Phase 12 forward-collection system stabilises

**Option C — Do nothing** (current state as of June 30 04:19 UTC restart):
- Cache is working. Drain is solved. Cost is now ~3,400 calls/day from the scheduler (first
  daily run fills cache; rest are cheap). V1 coordinator cycle runs at 0 API cost.
- Risk: if execution_runtime.py crashes again before cache is filled next day, the first
  hourly run after the day rolls over makes 2,480 calls (acceptable, not catastrophic).

**If Option A is taken:** the recommendation is to also shelve the coordinator cycle if it
begins generating errors (the HARD ASSERTION `predictions == 0 → RuntimeError` will fire if
all NS fixtures are settled without new upcoming ones).

---

## Task 2 — Tasmania NPL (league 648) Probe, July 3–4

**Status: Scheduled.**

### What was set up

New script: `scripts/probe_forward_odds.py` (also available for Norway Task 3).

New cron entries in `/etc/cron.d/bootball`:
```
0 6 3 7 * root  probe_forward_odds.py --league-ids 648 --days-ahead 3
0 6 4 7 * root  probe_forward_odds.py --league-ids 648 --days-ahead 2
```

**July 3 06:00 UTC:** ~24.5h before July 4 04:30 kickoffs, ~46.5h before July 5 04:30.
**July 4 06:00 UTC:** ~22.5h before July 5 04:30 kickoff.

Logs to `/var/log/bootball/probe_tasmania.log`.

### What the probe does differently from capture_forward_odds.py

1. **No bookmaker filter on fetching.** Fetches raw API response and logs ALL bookmaker names
   and ALL `bet_name` strings present — including unrecognised ones (addresses the Phase 11b
   "unverified bet-name strings" flag).

2. **Three-way outcome classification per fixture:**
   - **Pinnacle present** → write to `odds_snapshots` (clock starts), log Pinnacle rows written
   - **Soft books only** → do NOT write to `odds_snapshots`; write
     `logs/soft_book_decision_needed.txt` with bookmaker list and decision prompt
   - **No odds** → log as Pinnacle-absent candidate; no DB write

3. **Soft-book flag is explicit.** If only soft books (e.g., Bet365 alone) appear, the script
   writes the decision flag and **stops**. It does not silently begin logging soft-book odds.
   The user must make the call: (A) run Track B on Bet365, (B) shelve as Pinnacle-absent,
   or (C) re-probe closer to kickoff.

### Tasmania fixture facts

5 NS fixtures in DB:
- 1529264, 1529265 → July 4 04:30 UTC
- 1529266, 1529267 → July 4 06:45 UTC
- 1529268          → July 5 04:30 UTC

All confirmed via direct API in Phase 12 Task 1. Pinnacle was absent 5 days out; probe at
24–48h will determine if timing was the issue or if Pinnacle structurally doesn't cover Tasmania NPL.

---

## Task 3 — Norwegian 3.Division (777/778/779) Probe, July 25

**Status: Scheduled.**

New cron entry:
```
0 8 24 7 * root  probe_forward_odds.py --league-ids 777,778,779 --days-ahead 2
```

**July 24 08:00 UTC:** ~24–30h before July 25 kickoffs (Norwegian fixtures expected ~13:00-17:00 UTC).

Logs to `/var/log/bootball/probe_norway.log`.

### Fixture window timing — confirmed

- Today (June 30): July 25 fixtures are 25 days out. NOT in DB yet. `_fetch_upcoming()` looks 7 days ahead.
- `_fetch_upcoming()` window reaches July 25 on **July 18** (18 + 7 = 25).
- The 2AM `daily_run.py` cron on **July 18** will fetch July 25 fixtures and upsert them to the DB.
- By July 24, the fixtures will have been in the DB for 6 days.
- The July 24 probe reads from DB → finds fixtures → fetches odds. Sequencing is correct.

Norwegian league membership confirmed:
- 777, 778, 779 all in `ALL_LEAGUE_IDS` with `get_season()` = 2026
- Last known NS fixtures: May 4, 2026 (summer break). Direct API confirmed 8 NS fixtures per
  group on July 25.

Same Pinnacle/soft-book detection logic applies. Norway is a more mature market than Tasmania;
Pinnacle coverage is more likely but not confirmed until the probe runs.

---

## Task 4 — Quota Headroom Verdict

### Empirical measurement (not prediction)

| Time (UTC) | Event | Used | Remaining |
|------------|-------|------|-----------|
| June 30 00:00 | Day reset | 0 | 75,000 |
| June 30 02:00 | 2AM cron start | 6,835 | 68,165 |
| June 30 02:08 | 2AM cron end | 9,294 | 65,706 |
| June 30 04:19 | execution_runtime.py restarted (CACHE_DIR fix in effect) | ~14,000 est. | ~61,000 est. |
| June 30 08:00 | odds_poll.log quota check | 16,899 | **58,101** |

**Between 02:08 and 04:19 (2h 11min):** the old process (pre-fix CACHE_DIR) made ~2 more hourly
`job_fetch_results` runs = ~4,960 calls. Consistent with observed 16,899 used by 08:00.

**From 04:19 (post-fix restart) to 08:00:** first scheduler run filled cache (2,480 calls);
three subsequent hourly runs hit cache (~40 each = 120). Total: ~2,600. Consistent.

### Forward projection for rest of June 30

From 08:00 UTC with 58,101 remaining:
- Hourly `job_fetch_results` (16 runs, 08:00-24:00): 16 × 40 = **640 calls**
- `job_fetch_fixtures` (next runs at ~10:19, 16:19, 22:19): 3 × 40 = **120 calls**
- `job_fetch_odds` (every 1h, Tasmania fixtures now in window): ~30 calls/run × 16 = **480 calls**
- Forward collection (`capture_forward_odds.py` if run today): **15 calls**
- **Projected remainder**: ~1,255 calls

**Projected end-of-day total: ~18,154 calls** — well under 75k.

### Verdict

**Collection fits. The drain is resolved — not by rate-limiting, but by the process restart.**

The CACHE_DIR fix is live in PID 120 (started June 30 04:19 UTC). Each day going forward:
- First `job_fetch_results` run after midnight: ~2,480 calls (cache miss for new date)
- Remaining 23 hourly runs: ~40 each = 920 calls
- 2AM cron: ~40 calls (cache filled by the 00:XX scheduler run already)
- **Steady-state daily total: ~3,500–4,500 calls** — 6% of the 75k limit

**Condition:** this assumes execution_runtime.py stays running (or restarts with the same
codebase). If it crashes and restarts after midnight but before the first hourly run fills
the cache for the new day, the first post-midnight run costs 2,480 calls (not catastrophic).

**If execution_runtime.py is NOT restarted (pre-fix process):** quota blows again by ~15:00 UTC
the next day. The answer would be: drain must be resolved first. That situation no longer
applies as of June 30 04:19 UTC.

---

## Summary of Deliverables

| Task | Status | Output |
|------|--------|--------|
| 1. Quota drain call chain | Complete | 0 API calls per 20-min coordinator cycle; drain was `job_fetch_results` (2,480 calls/hr) in APScheduler; now resolved by restart with CACHE_DIR fix |
| 1. V1/V2 classification | Complete | coordinator cycle = pure V1; APScheduler data jobs = V2-relevant but coupled to same process |
| 1. Recommendation | Reported | Option A (rate-limit to 6h) or Option C (leave as-is, restart fixed it). Retire requires decoupling. |
| 2. Tasmania probe July 3–4 | Scheduled | `/etc/cron.d/bootball` July 3 + July 4 at 06:00 UTC; logs to `/var/log/bootball/probe_tasmania.log`; soft-book flag → `logs/soft_book_decision_needed.txt` |
| 3. Norway probe July 24 | Scheduled | `/etc/cron.d/bootball` July 24 at 08:00 UTC; fixtures enter window July 18; logs to `/var/log/bootball/probe_norway.log` |
| 4. Quota headroom | Measured | 58,101 remaining at 08:00 UTC; projected ~18k calls day total; collection fits comfortably |
