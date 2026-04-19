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
9. **Test file organization** - Each module has tests in `tests/` directory alongside source
10. **Verify imports work first** - Always run `python -c "from module import something"` before assuming code loads

### Event-Driven Architecture Lessons
1. **Events are immutable** - Once created, event payload should not be modified
2. **Handler errors shouldn't block emission** - Wrap handler calls in try/except
3. **Event types as enum** - Use EventType enum for type safety and discoverability
4. **Global emitter pattern** - Singleton emitter for simple in-process events
5. **Convenience emit functions** - Helper functions make events easier to emit correctly
6. **Subscription cleanup** - Call unsubscribe when handler is no longer needed

### Security Lessons
1. **XSS prevention** - Sanitize ALL user-rendered content before display
2. **Validate before sanitizing** - Check for suspicious patterns BEFORE HTML encoding
3. **Log injection** - Remove newlines/carriage returns from log inputs
4. **Rate limiting** - Sliding window better than fixed window for smooth limiting
5. **Event signatures** - HMAC-SHA256 + timestamp + sequence for replay protection
6. **Security headers** - CSP, X-Frame-Options, etc. protect against common attacks

### Testing Lessons
1. **Run tests after implementation** - Don't wait until end to test
2. **Test both success and failure paths** - Check invalid inputs are rejected
3. **pytest available** - Use `/opt/projects/bootball/.venv/bin/python -m pytest`
4. **Import test ordering** - Tests should be order-independent

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
2. **Calibration matters MORE than accuracy** - Research: +34.69% ROI for calibration vs -35.17% for accuracy
3. **Value bets need confidence** - Model not confident enough; need better probability estimates
4. **Isotonic calibration improves Brier** - 0.307 → 0.230 on test data
5. **xi parameter tuning** - 0.01 is optimal for time decay
6. **Train/test split critical** - Dixon-Coles fits on ALL data without explicit split
7. **Probability ≠ Confidence** - Probability is point estimate; confidence is how much we trust it
8. **Calibration hierarchy**: Raw Model → Isotonic Calibration → Confidence Interval → Kelly Sizing
9. **Brier Score targets**: < 0.25 for 3-outcome, < 0.20 for 2-outcome markets
10. **Current gap**: We show raw probability as "confidence" - need calibrated probability + confidence interval

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
- 50 leagues, 76,170 finished fixtures, 1,120,645 events (14.7 avg per matches)
- All remaining leagues have proper API coverage (verified via backfill results)

### Sweet Spot Research (April 2026)
1. **Optimal odds range**: 1.8-2.2 for BTTS (research from SmartBettingStats)
2. **Why this range**: Bookmakers underprice at these odds; public bets more on extremes
3. **Implementation**: Added "🌟 Sweet" badge for odds 1.8-2.2 with positive EV
4. **Filter added**: "🌟 Sweet Spot" checkbox to show only sweet picks
5. **Evidence**: Backtest showed +37.84 units profit at these odds with 10%+ edge requirement

### Calibration Research Summary
- **Key finding**: "A well-calibrated 55% prediction that wins 55% beats a confident 70% that wins 50%"
- **Our current issue**: Displaying raw model probability as "confidence" (wrong)
- **Correct display**: Calibrated probability + confidence interval + evidence strength
- **Research file**: `docs/research/details/model_calibration.md`

### Market Research Summary (All Markets)
| Market | Difficulty | Best Odds | Research Finding |
|--------|------------|-----------|-----------------|
| BTTS | EASIER | 1.85-2.20 | +37.84 units backtest |
| O/U 2.5 | MODERATE | 1.70-1.95 | Bundesliga 60-65% hit rate |
| O/U 1.5 | EASY | 1.20-1.40 | Very high hit rate, low value |
| H2H (1X2) | HARDEST | 2.00+ | Bookmakers expert at these |
| Asian Handicap | HARD | 1.90-2.10 | Complex, sharp market |
| Correct Score | HARDEST | 3.00+ | High variance |