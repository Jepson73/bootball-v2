# Phase 11b Findings — 2026-06-29

Investigation + targeted fixes across five tasks.

---

## Task 1 — Pinnacle absence on 80 PlacedBets: verdict

**Question:** Are the 80 bets (69 unique fixtures) with no Pinnacle row in `FixtureOdds` a real
coverage gap or a capture artifact (timing, parse gap, pagination)?

**Method:** Three-layer check on the raw evidence:
1. Timestamp delta (date fixture fetched vs date played) to identify early-fetch timing artifacts.
2. Raw cached API responses for representative near-time affected fixtures — checked directly in
   `data/raw/api_cache/api_cache/` after fixing the CACHE_DIR path bug (see Task 3 note).
3. Pagination field in the raw API response to rule out a truncated page-1-only fetch.

**Verdict: ~14 timing artifacts (~20%), ~55 genuine Pinnacle absences (~80%).**

### Group A — Timing artifacts (~14 fixtures)

11 fixtures appear with only the `"api"` bookmaker (API-Football's internal prediction aggregate).
These were fetched 5–7 days before kickoff when Pinnacle had not yet posted odds for the fixture.
Evidence: EPL fixtures 1379300–1379302 fetched April 16, kickoff April 21–22. Non-affected EPL
fixtures fetched on game day showed 8+ real bookmakers including Pinnacle.

3 additional fixtures were fetched 3–7 days before kickoff and show soft bookmakers (Unibet, 1xBet)
but no Pinnacle — consistent with Pinnacle posting odds later in the pre-match window.

**Implication:** If these ~14 fixtures had been captured closer to kickoff, Pinnacle odds would
likely have been present. These bets received no CLV at time of capture; if closing-line capture
runs again on them, Pinnacle might now be available (but the match is already FT so the API returns
no odds for completed fixtures).

### Group B — Genuine Pinnacle absence (~55 fixtures)

23 fixtures across 14 leagues (87, 173, 289, 384, 403, 421, 425, 568, 585, 782, 828, 845, 1025,
1174) where Pinnacle does not appear for ANY fixture in `FixtureOdds` for those leagues.

32 fixtures in leagues where Pinnacle covers other fixtures but not these specific matches — fetched
on game-day with multiple real bookmakers present, no Pinnacle.

**Raw API evidence (three fixtures verified from cache):**

| Fixture | League | Bookmakers in raw response | Paging | Pinnacle |
|---------|--------|---------------------------|--------|---------|
| 1540968 | 172 | Unibet, 1xBet, Dafabet (3 total) | 1/1 | ABSENT |
| 1521130 | 291 | WH, Bet365, Marathonbet, Unibet, 1xBet, Betano, Superbet, Dafabet (8) | 1/1 | ABSENT |
| 1541006 | 172 | Unibet, 1xBet (2 total) | 1/1 | ABSENT |

`paging={current:1, total:1}` confirms there is no second page — Pinnacle's absence is not a
pagination artifact. The API simply does not return Pinnacle for these fixtures.

**Implication:** For ~55 bets, CLV is structurally unavailable. The Pinnacle gate in
`capture_closing_lines()` is correct: returning `None` instead of a soft-book figure.

---

## Task 2 — Historical CLV: recomputation finding

**Question:** Do any of the 17 PlacedBets with `clv_pct` populated use a non-Pinnacle reference
that would need to be stripped?

**Finding: No recomputation needed. All 17 are already Pinnacle-only.**

```
PlacedBets with clv_pct non-null:  17 / 448
All 17 have a Pinnacle row in FixtureOdds: YES
Any with non-Pinnacle FixtureOdds source:  0
Mean CLV:   +5.85%
Positive:   14 / 17 (82%)
```

The 80 affected bets without Pinnacle rows simply never had `clv_pct` populated — they received
`None` from `capture_closing_lines()` because there was no FixtureOdds row to join. There was no
soft-book contamination in historical data; the Phase 11 Pinnacle gate only affects future captures
and prevents a future contamination path.

**Note on the +5.85% mean vs Phase 8 baseline:**
Phase 8 reported −2.02%/−3.77% CLV against Pinnacle on a systematic retrospective sample.
The 17-bet figure here is a small self-selected subset (bets where Pinnacle coverage happened to
exist) and is not statistically comparable to Phase 8's analysis. N=17 yields wide confidence
intervals; no conclusions about model edge should be drawn from this figure.

---

## Task 3 — Quota-aware scheduling: implemented

Four changes were made:

**1. CACHE_DIR path bug fixed (`src/ingestion/client.py`)**

All cached API responses were stored in `data/raw/api_cache/api_cache/` (the inner, writable dir
owned by `bootball`) but `CACHE_DIR` pointed to the outer `data/raw/api_cache/` (owned by
`nobody:nogroup`). Every cache lookup was a miss, causing every API call to go live — even for
data fetched weeks ago. `CACHE_DIR` is now `Path("data/raw/api_cache/api_cache")`. Before fix:
~3 million quota calls consumed with no cache benefit. After fix: historical responses are served
from disk.

**2. Forward-league season mapping fixed (`config/settings.py`)**

Norwegian 3rd Division (leagues 777/778/779) and Tasmania NPL (league 648) run on calendar year
(2026) but were absent from `calendar_year_leagues`. `settings.get_season(777)` was returning
2025 while the DB had 2026 fixtures. Added all four league IDs to `calendar_year_leagues`.

**3. `backfill_daily_cap = 60000` setting added (`config/settings.py`)**

Soft cap for backfill jobs. Forward-collection calls and real-time calls may use the full 75,000
daily limit. The cap enforces ≥20% headroom (≥15,000 calls) for forward collection regardless
of backfill volume.

**4. Quota check + daily log in `scripts/daily_run.py`**

- `_fetch_completed()` now checks `calls_used_today() >= settings.backfill_daily_cap` before each
  league iteration. When the cap is hit, backfill stops with a `[QUOTA] Backfill paused` log line
  and appends to `logs/quota_log.csv`.
- Run start and run end are logged to `logs/quota_log.csv` with `calls_used`, `calls_remaining`,
  `daily_limit`, `backfill_cap` columns.

---

## Task 4 — Forward collection verification: BLOCKED

**`odds_snapshots` count: 0**

Clock has not started. Two blockers identified:

**Blocker A — Season mapping bug (now fixed):** `daily_run.py` fetched upcoming fixtures for
forward leagues with `season=2025` instead of `season=2026`. No NS fixtures would be returned from
the API for the wrong season. Fixed in Task 3 item 2 above.

**Blocker B — DB stale:** All 140 fixtures across the four forward leagues (35 per league) are
FT with the most recent dated 2026-05-04. Norwegian 3rd Division runs April–November, so June–
November fixtures should exist in the API. Tasmania NPL runs March–October. Neither league has
upcoming fixtures in the DB.

**Action required:** Run `daily_run.py` once after today's API quota resets to populate upcoming
fixtures. With the season mapping and cache path now fixed, this run will correctly fetch
`season=2026` NS fixtures for leagues 777/778/779/648 and write them to the DB. Then run
`capture_forward_odds.py` to verify `SELECT count(*) FROM odds_snapshots > 0`.

**Bet-name mapping still unverified:** `capture_forward_odds.py` has never processed a live
payload. Run with `DEBUG` logging on the first live capture to confirm `bet_name` strings match
the handler branches ("Match Winner"/"1x2", "Goals Over/Under"/"Over/Under", "Both Teams Score").

---

## Task 5 — h2h probability vector persistence: implemented

Three changes applied:

**1. Migration 027 (`migrations/027_prob_vector_prediction_records.sql`)**

Adds `prob_home REAL`, `prob_draw REAL`, `prob_away REAL` columns to `prediction_records`.
Applied successfully to the live DB.

**2. `PredictionRecord` model updated (`src/storage/models.py`)**

Three nullable `Float` columns added after `blended_prob`. NULL for binary markets (btts, ou25, ou15).

**3. `save_predictions()` updated (`src/prediction/unified_prediction_service.py`)**

For `market == "h2h"`, maps `predicted_probs` dict keys to the new columns:
- `prob_home` ← `predicted_probs.get("1")`  (API-Football key for Home win)
- `prob_draw` ← `predicted_probs.get("X")`  (Draw)
- `prob_away` ← `predicted_probs.get("2")`  (Away win)

**`evaluate_track_a()` caller note:** For h2h records, pass `prob_home = record.prob_home`.
Existing settled records have NULL in all three columns (they predate this migration); those
records will fall into `skipped_no_prob_home` in the current evaluation run.

---

## Bugs found (additional, not in original brief)

| Bug | Impact | Fix |
|-----|--------|-----|
| `CACHE_DIR` path mismatch — all cached files unreachable | Every API call went live; quota consumed with no cache benefit | Fixed: `CACHE_DIR = Path("data/raw/api_cache/api_cache")` |
| Forward league season mismatch — 777/778/779/648 not in `calendar_year_leagues` | `daily_run.py` fetched `season=2025` for Norwegian and Tasmania leagues; no upcoming fixtures populated | Fixed: added all four IDs to `calendar_year_leagues` |
