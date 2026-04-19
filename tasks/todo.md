# Tasks / Todo - Football Prediction System

---

## CURRENT PRIORITY: Web UI Rebuild

### Status: IN PROGRESS - Most fixes applied

**Fixed Issues:**
- ✅ `btts_yes` → `odd_btts_yes` in api_predictions (line 541)
- ✅ Added `@app.before_request` to call `load_caches()` before each request
- ✅ Fixed DetachedInstanceError in betting_page - extracted `round_number` before session closes
- ✅ Fixed `None` odds issue in api_predictions - skip fixtures without odds

**Remaining Issues:**
- [ ] Verify all pages work with gunicorn
- [ ] Commit to git for rollback safety

---

## UI Research Summary (from agent)

### Recommended Hybrid Approach

**For Betting + Admin pages**: "Command Center" (Technical/Bot Focus)
- Dashboard with real-time widgets
- Bot status indicator prominent
- Bankroll, Active Bets, Today's Predictions cards
- Admin with model performance, API health

**For Predictions + Tracking pages**: "Card-Based" (Clean/Scannable)
- Left sidebar navigation
- Match cards with confidence bars
- Timeline view for tracking predictions→results

### Color System
```
Background:  #0d1117 (dark)
Cards:       #161b22 (slightly lighter)
Borders:    #30363d (subtle)
Win:         #3fb950 (green)
Loss:        #f85149 (red)
EV+:         #58a6ff (blue)
Pending:     #d29922 (amber)
```

### Page Layouts

**1. Predictions Page**
- League filter dropdown + time range selector
- Market tabs: 1X2 | BTTS | O/U 2.5 | O/U 1.5
- Prediction cards: Team names, time, league badge, prediction, confidence bar, EV badge
- Cards with green glow = positive EV, red border = negative EV

**2. Betting Page (Bot Automated)**
- Bankroll card (large, prominent): Balance, ROI, Round #
- Bot status indicator: Running/Paused/Error with last action time
- Pending bets table: Match, Market, Pick, Stake, Odds, EV
- Buttons: Place Bets (auto), Settle Bets, Manual Override
- Recent activity log (collapsible)

**3. Tracking Page**
- Date range filter + market filter + league filter
- Timeline view: prediction → result
- Win/Loss/EV per bet with color coding
- Running ROI calculation
- Stats cards: Win Rate, Total Bets, Avg EV, CLV

**4. Admin Panel**
- System health cards: API calls remaining, DB status, Last daily run
- Actions: Run Daily Run, Train Models, Place Bets, Settle
- League management: Enable/disable leagues
- Config editor

**5. Debug Page**
- Terminal-style log output
- Recent API responses
- Model performance metrics
- Fixture/odds data inspection

---

## BACKLOG (After UI Rebuild)

### Phase A: Core Data Pipeline
- [ ] Fix settle_fixtures.py - runs every 2 hours
- [ ] Update predictions in background during settle_fixtures
- [ ] Fix daily_run to only run once daily (not twice hourly)

### Phase B: Prediction Caching
- [ ] Predictions stored in DB, not computed on page load
- [ ] Background job updates stale predictions (>4 hours)
- [ ] Page loads instantly from cache

### Phase C: Bankroll Tracking
- [ ] Balance = initial + settled_pnl - pending_stake (working)
- [ ] Add total_staked to round stats
- [ ] Track ROI properly

### Phase D: Alerts System
- [ ] Discord alerts show market name
- [ ] Limit to 2 alerts per settle run
- [ ] End each alert with separator line (---)

### Phase E: Cron Jobs
- [ ] daily_run: once daily at 4 AM
- [ ] settle_fixtures: every 2 hours (fetch completed, settle bets, update predictions)
- [ ] auto_bet --bet-only: every 2 hours (check balance first)

### Phase F: Git/Rollback
- [ ] Commit web_ui.py after rebuild
- [ ] Add tasks/lessons.md for lessons learned
- [ ] Document all scripts in docs/

---

## Completed Items (for reference)

### Phase 1-5: MVP ✅
- DB, backfill, fixtures
- Elo, form, strength, xG features
- Dixon-Coles, ML ensemble models
- Shin, EV, Kelly, value detection
- Brier Score: 0.230 (target < 0.25)

### Phase 8: Production Pipeline ✅
- daily_run.py pipeline
- API budget management
- SQLite backup

### Phase 10: Betting Markets ✅
- Market definitions (H2H, BTTS, O/U)
- Prediction models for all markets
- Calibration using isotonic regression

### Phase 13: Winning Markets ✅
- League targeting (Bundesliga, Eredivisie high-scoring)
- BTTS + Over 2.5 combo
- League-specific weightings

### Phase 15: Bot Core ✅
- Historical backtesting
- Alert system (Telegram/Slack)
- Bankroll tracking tables

### Phase 16: Data Fixes ✅
- SQLAlchemy session bug fixed
- Team names now load correctly
- Unreliable badge changed to rounded rectangle

### Phase 17-19: Operations ✅
- Maintenance operation works
- Train model works (4976 samples)
- place_bets, settle_bets tested

---

## Priority Order
1. **NOW**: Rebuild web_ui.py working (server down)
2. **NEXT**: Fix cron jobs (settle_fixtures, daily_run)
3. **LATER**: Add prediction caching
4. **FUTURE**: Phase 11 Multi-user

---

## Notes from Workflow
- Always restart server after web_ui.py changes
- Test with curl before assuming it works
- Commit before major rewrites
- Keep predictions fast = cache in DB