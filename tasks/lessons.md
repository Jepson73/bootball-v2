# Lessons Learned

## Corrections & Patterns

### Development Lessons
1. **Always check session state** - SQLAlchemy objects can't be used outside session context. Extract data before closing session.
2. **Check .env loading** - pydantic-settings needs `model_config` with `env_file` to load .env properly
3. **Use merge() or check existence** - Don't assume DB objects don't exist; check before insert to avoid duplicate key errors
4. **Test imports first** - Always verify packages installed (numpy, scipy, sklearn) before running new code
5. **Use math.factorial not np.math** - numpy doesn't have math submodule in newer versions
6. **Fix bugs incrementally** - Don't build everything before testing; fix errors as they appear
7. **session.merge() uses PK not unique constraints** - merge() checks primary key; for unique constraints (league_id, season, team_id), must query first
8. **Standings upsert pattern** - Query by unique columns first, update existing or insert new

### API-Football Lessons
1. **Rate limiting** - API has daily limits (100k calls/day); cache responses to save calls
2. **Batch fetching** - Use `?ids=id1-id2-...` endpoint to fetch up to 20 fixtures at once
3. **Odds history** - Only 7 days of odds stored; must capture as they come in
4. **Live odds** - Disappear permanently after match ends; no historical retrieval
5. **League IDs** - Must discover via API, not hardcoded; use `/leagues?current=true`
6. **Team IDs can duplicate across leagues** - Same team ID used in multiple leagues; handle duplicates in backfill

### Today's Fixes (web_ui and xg errors)
1. **NULL comparison** - Check BOTH goals_home AND goals_away for None before comparison
2. **xG proxy from shots** - Mathematically sound: xG = shots_on_target × 0.33 (empirically ~33% conversion rate)
3. **xG fallback chain**: xG API > shots proxy > actual goals > 0
4. **BTTS formula** - P(BTTS) = P(home>0) * P(away>0), NOT 1 - P(home=0)*P(away=0)
5. **Return neutral when no data** - Return (0.5, 0.5) instead of fake predictions
6. **Backfill duplicates** - Check team exists before insert to handle same team in different leagues
7. **Database leagues** - web_ui now fetches predictions for ALL leagues with data, not just TIER1
8. **compute_ev None** - Can't compare None <= 0; must check "if odd is None or odd <= 0"
9. **Odds dict values** - Ensure all odds values are numeric (0 if None) before computing EV
10. **API bet_type IDs** - Correct mapping: h2h=1, btts=8, over_under=5 (NOT 5,4,5!)
    - bet_type=5 is "Goals Over/Under"
    - bet_type=8 is "Both Teams Score"

### BETTING STRATEGY LESSONS (Research-Validated)
The CORRECT two-step approach:
1. **Step 1**: Predict using model (team strengths, xG, form, home advantage)
   - Output: probability for each outcome (e.g., Home 55%, Draw 25%, Away 20%)
2. **Step 2**: Find best odds - compare model probability vs bookmaker implied probability
   - If model says 55% but odds imply 50% → value exists

Common MISTAKES to avoid:
- ❌ Finding value bets FIRST, then trying to justify prediction
- ❌ Only betting when value >5% (misses many good predictions)
- ❌ Chasing high odds without proper prediction confidence

Key insight: A confident prediction (e.g., 60%+) at fair odds is a valid bet - you don't NEED value to bet, you need a GOOD PREDICTION first.

### Model Training Lessons
1. **More data = better models** - 380 matches insufficient; need 50k+ for proper training
2. **Calibration matters** - Brier Score 0.87 too high; target < 0.25
3. **Value bets need confidence** - Model not confident enough; need better probability estimates
4. **Isotonic calibration improves Brier** - 0.307 → 0.230 on test data
5. **xi parameter tuning** - 0.01 is optimal for time decay
6. **Train/test split critical** - Dixon-Coles fits on ALL data without explicit split

---

## Session Start Review

- [x] Review relevant lessons before starting work - Done
- [x] Check API call budget remaining
- [x] Verify database connection works
- [x] Review current todo.md progress
- [x] Run any fixes from last session

### Before Marking Done: QA Checklist
- [x] Does it work manually? (test predictions)
- [x] Are there errors in logs? (check for NoneType warnings)
- [x] Does server restart cleanly? (kill and start fresh)

### Model Architecture Lessons (Phase 20)
1. **Don't use `round` as parameter** - shadows Python's built-in `round()` function
2. **Each market needs separate model** - 1X2, BTTS, OU15, OU25 use different features/results
3. **BTTS formula**: P(BTTS) = P(home>0) × P(away>0), NOT 1-P(home=0)×P(away=0)
4. **Poisson for goals, not BTTS** - Independence assumption breaks for BTTS correlations
5. **Separate cache files** - model_h2h.pkl, model_btts.pkl, model_ou15.pkl, model_ou25.pkl

### Late Goal Research (Phase 20)
- **23-26% of goals scored in final 15 min (75+ min)** - consistent across top leagues
- **Highest late goal leagues**: J1 League (26.0%), Segunda División (25.5%), 1. Lig (25.3%), La Liga (25.2%)
- **85+ min (stoppage time)**: ~12-15% of all goals
- **A-League has NO event data** - 326 fixtures but 0 events in fixture_events table
- **Late goal edge potential**: These leagues could have in-play betting value (BTTS, Over 2.5 late in match)

### Future Betting Markets (Research)
- **First Half Goals**: Markets exist for over/under goals in 1st half
- **Second Half Goals**: Markets exist for over/under goals in 2nd half  
- **Time Interval Markets**: In-play betting on specific minute ranges (e.g., 75-80, 80-85, 85-90)
- **Implementation**: These require separate models and would need event-level data (minute-by-minute goals)

### Backfill Data Validation (May 2026)
- **Root cause of missing events**: Backfill only saved events for NEW fixtures, not existing ones
- **Fix implemented**: Check `_events_exist()` before adding, not just `_fixture_exists()`
- **New methods added**:
  - `get_data_coverage(league_id)` - returns dict with fixtures, events, stats, odds counts
  - `backfill_events_for_existing()` - batch fetch events for fixtures missing them
  - `backfill_stats_for_existing()` - batch fetch stats using efficient batch API
  - `backfill_all_missing()` - combined method that fetches all missing data in one pass
- **Efficiency**: Uses batch API (20 IDs per call) rather than individual calls per fixture
- **Verified**: A-League now has 5074 events (was 0), stats added for 145 fixtures
- **League removal**: Third NL - Istok (213) removed - API shows fixtures:false for all data types

### Current Data Quality
- 50 leagues, 76,170 finished fixtures, 1,120,645 events (14.7 avg per match)
- All remaining leagues have proper API coverage (verified via backfill results)