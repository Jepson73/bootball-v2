# Tasks / Todo - Event-Driven Football Prediction System

---

## ARCHITECTURE: Event-Driven Architecture

### Why Event-Driven?
- **Real-time updates**: WebSocket support for live predictions/odds
- **Multi-user**: Natural fan-out to multiple clients
- **Scalability**: Decoupled handlers can scale independently
- **Audit trail**: Events provide built-in history

### Core Principles
1. Events are immutable facts
2. Handlers react to events, never block emission
3. Caching layered on top for performance
4. Market-specific models trained independently

---

## PHASE 0.5: Security Foundation (Week 0.5)

### 0.5.1 Security Research
- [ ] Create `docs/research/security/threat_model.md` - document attack surfaces
- [ ] Create `docs/research/security/mitigations.md` - threat vs mitigation matrix
- [ ] Review OWASP Top 10 for web apps
- [ ] Review API-Football security requirements

### 0.5.2 Input Validation Layer
- [ ] Create `src/security/validation.py` - schema validation for all inputs
- [ ] Create `src/security/sanitization.py` - XSS prevention, SQL injection helpers
- [ ] Tests: `tests/security/test_validation.py`
- [ ] Apply validation to web_ui.py endpoints

### 0.5.3 Event Security
- [ ] Create `src/security/event_signing.py` - HMAC event signatures
- [ ] Verify event signatures in handlers before processing
- [ ] Add replay protection (sequence numbers or timestamps)
- [ ] Tests: `tests/security/test_event_signing.py`

### 0.5.4 Web Layer Security
- [ ] Add rate limiting to all endpoints (per IP, per user)
- [ ] Create `src/security/rate_limit.py` - sliding window limiter
- [ ] Add security headers to web_ui.py:
  - Content-Security-Policy
  - X-Frame-Options: DENY
  - X-Content-Type-Options: nosniff
  - Strict-Transport-Security
- [ ] Tests: `tests/security/test_rate_limit.py`

### 0.5.5 Authentication
- [ ] Document current auth weaknesses
- [ ] Plan JWT vs session-based auth for multi-user
- [ ] Create `docs/research/security/auth_strategy.md`

### 0.5.6 API Key Management
- [ ] Document API key storage现状
- [ ] Plan secret rotation procedure
- [ ] Create `scripts/rotate_api_key.py` - secure key rotation

### 0.5.7 Security Testing
- [ ] Run `pytest tests/security/` - all security tests pass
- [ ] Manual pen-test checklist completion
- [ ] Git tag: `v0.0-security`

### Security Checklist
```
[ ] XSS prevention in all user inputs
[ ] SQL injection prevention (use parameterized queries)
[ ] Rate limiting on all endpoints
[ ] Security headers in responses
[ ] Event payload validation before processing
[ ] Event signature verification before handling
[ ] No secrets in git history
[ ] API keys in secure env only
[ ] Input validation on all external data
```

---

## PHASE 1: Event Foundation (Week 1-2)

### 1.1 Event Base System
- [x] Create `src/events/base.py` - BaseEvent class with timestamp, type, payload
- [x] Create `src/events/registry.py` - Event type registry (via EventType enum)
- [x] Tests: `tests/events/test_base.py`
- [x] Manual test: Emit event, verify handlers called

### 1.2 Fixture Events
- [x] Create `src/events/fixture_events.py`
  - `FixtureScheduled` - New fixture added
  - `FixtureUpdated` - Fixture details changed
  - `FixtureCompleted` - Match finished (FT)
- [ ] Create `src/handlers/fixture_handler.py`
- [x] Tests: `tests/events/test_fixture.py`
- [ ] Manual test: Fetch fixture → emit event → verify DB updated

### 1.3 Odds Events
- [x] Create `src/events/odds_events.py`
  - `OddsUpdated` - Odds changed for fixture
  - `OddsStale` - Odds older than threshold
- [ ] Create `src/handlers/odds_handler.py`
- [ ] Tests: `tests/events/test_odds.py`
- [ ] Manual test: Update odds → emit event → verify predictions recalculated

### 1.4 Settlement Events
- [x] Create `src/events/prediction_events.py`
  - `PredictionCreated` - New prediction generated
  - `PredictionSettled` - Prediction resolved
- [ ] Create `src/handlers/settlement_handler.py`
- [ ] Tests: `tests/events/test_settlement.py`
- [ ] Manual test: Complete fixture → settle predictions → verify P&L

### 1.5 Model Events
- [x] Create `src/events/model_events.py`
  - `ModelTrained` - New model version trained
  - `ModelDegraded` - Drift detected
  - `ModelActivated` - Model made live
- [ ] Create `src/handlers/model_handler.py`
- [ ] Tests: `tests/events/test_model.py`

### 1.6 Git Checkpoint
- [x] Commit all Phase 1 work
- [ ] Tag: `v0.1-event-base`

---

## PHASE 2: Handler Infrastructure (Week 3-4)

### 2.1 Settle Handler (From daily_run)
- [x] Refactor `scripts/settle_fixtures.py` to emit events
- [x] Create `src/handlers/settlement_handler.py`
  - Consumes `FixtureCompleted`
  - Updates predictions
  - Calculates P&L
- [ ] Tests: `tests/handlers/test_settle.py`
- [ ] Manual test: Run settle → verify all events processed

### 2.2 Odds Handler
- [x] Create `src/handlers/odds_handler.py`
  - Consumes `OddsUpdated`
  - Triggers prediction recalculation if EV changed
- [ ] Tests: `tests/handlers/test_odds.py`
- [ ] Manual test: Poll odds → verify predictions update

### 2.3 Backfill Handler
- [x] Create `src/handlers/backfill_handler.py`
  - Handles historic data recovery
  - Short-term backfill (2h-24h history)
- [ ] Tests: `tests/handlers/test_backfill.py`

### 2.4 Git Checkpoint
- [x] Commit all Phase 2 work
- [ ] Tag: `v0.2-handlers`

---

## PHASE 3: Prediction Caching (Week 5-6)

### 3.1 Cache Infrastructure
- [ ] Create `src/cache/prediction_cache.py`
  - TTL-based cache per market
  - Cache invalidation on events
  - Market-specific TTLs (BTTS: 2h, H2H: 1h)
- [ ] Tests: `tests/cache/test_prediction.py`

### 3.2 Cache Integration
- [ ] Integrate cache into web_ui predictions API
- [ ] Event-driven cache invalidation
- [ ] Tests: `tests/cache/test_integration.py`
- [ ] Manual test: Load predictions → verify cache works

### 3.3 Git Checkpoint
- [ ] Commit all Phase 3 work
- [ ] Tag: `v0.3-cache`

---

## PHASE 4: Multi-Market Expansion (Week 7-8)

### 4.1 API-Football Market Discovery
- [ ] Research all bet_type IDs from API
- [ ] Document markets in `docs/markets/`
- [ ] Create placeholder event types for future markets

### 4.2 Markets to Implement
Based on API-Football coverage:

**Current Markets (Working):**
- H2H (bet_type=1) - Match Winner ✅
- BTTS (bet_type=5) - Both Teams To Score ✅
- O/U 2.5 (bet_type=4) - Over/Under 2.5 ✅

**Markets to Add:**
- O/U 1.5 (bet_type=?) - Over/Under 1.5 ⚠️
- Asian Handicap (bet_type=3) - Line-based handicap ⚠️
- Correct Score (bet_type=6) - Exact score ⚠️
- HT/FT (bet_type=7) - Half Time/Full Time ⚠️
- Double Chance (bet_type=?) - 2 of 3 outcomes ⚠️
- Draw No Bet (bet_type=?) - No draw option ⚠️
- Goalscorer (bet_type=?) - First/anytime scorer ⚠️

### 4.3 Feature Requirements per Market
```
Market         | Features Needed
--------------|----------------------------------
H2H           | Elo, form, home/away splits, h2h
BTTS          | Goals scored, goals conceded, form
O/U 1.5       | Scoring frequency, low-scoring teams
O/U 2.5       | Average goals, form
Asian Handicap| Elo difference, home advantage
Correct Score | Poisson, goal distribution
HT/FT         | First half form, second half form
Goalscorer    | Player goals, assists, injuries
```

### 4.4 Git Checkpoint
- [ ] Commit market research
- [ ] Tag: `v0.4-markets`

---

## PHASE 5: Model Training Infrastructure (Week 9-10)

### 5.1 Calibration Infrastructure (CRITICAL - Research Shows +34.69% ROI)
- [ ] Create `src/models/calibrator.py`
  - Isotonic regression calibrator per market
  - Platt scaling fallback
  - Temperature scaling for neural nets
- [ ] Track calibration metrics in DB
  - Brier score per market per week
  - ECE (Expected Calibration Error)
  - Reliability diagram data
- [ ] Tests: `tests/models/test_calibration.py`

### 5.2 Market-Specific Training
- [ ] Create `src/models/trainer.py` improvements
  - Market-specific feature selection
  - Per-market calibration tracking
- [ ] Add market parameter to training pipeline
- [ ] Calibrate ALL predictions before serving

### 5.3 Confidence Intervals
- [ ] Calculate confidence per prediction
  - Sample size (how much training data)
  - Model agreement (ensemble variance)
  - Data quality (missing fields %)
- [ ] Display confidence in UI
  - Low/Medium/High badge
  - Confidence interval range
- [ ] Tests: `tests/models/test_confidence.py`

### 5.4 Drift Detection
- [ ] Create `src/models/drift_detector.py`
  - Brier score per market over time
  - Alert threshold per market
- [ ] Auto-retrain calibrator when drift detected
- [ ] Tests: `tests/models/test_drift.py`
- [ ] Manual test: Degrade model → verify detection

### 5.5 Git Checkpoint
- [ ] Commit training infrastructure
- [ ] Tag: `v0.5-calibration`

---

## PHASE 6: Real-time Web (Week 11+)

### 6.1 WebSocket Integration
- [ ] Add WebSocket support to web_ui.py
- [ ] Event broadcast to connected clients
- [ ] Tests: `tests/web/test_websocket.py`

### 6.2 Multi-User Support
- [ ] Session management
- [ ] User-specific alerts
- [ ] Personal bet history
- [ ] Tests: `tests/web/test_multiuser.py`

### 6.3 Discord Alerts
- [x] Create `src/alerts/discord.py` - Discord webhook alerts
- [x] Create `scripts/send_alerts.py` - CLI for sending top bets
- [x] Add settings for webhook URL and alert preferences
- [x] Tag: `v0.6-alerts`

### 6.4 Git Checkpoint
- [ ] Commit real-time features
- [ ] Tag: `v1.0-realtime`

---

## PHASE 7: User Following & Manual Selection (Week 12+)

### Why This Phase?
1. **Real-time tracking**: Users select specific fixtures to follow, not all
2. **Multi-user isolation**: Each user sees only their selected fixtures
3. **Personalization**: Users can create watchlists, tag fixtures
4. **Betting bot control**: Manual override of automated selection

### 7.1 User Selection Model
- [ ] Create `WatchedFixture` model
  - user_id: Optional (for multi-user future)
  - fixture_id: Foreign key
  - market: Which market to watch
  - selection_type: 'manual' | 'auto' | 'watch'
  - status: 'pending' | 'live' | 'settled'
  - created_at, updated_at
- [ ] Create `UserPreference` model
  - user_id (future)
  - markets: List of preferred markets
  - leagues: List of preferred leagues
  - ev_threshold: Personal EV threshold

### 7.2 Selection API
- [ ] `POST /api/select` - Select fixture+market to follow
- [ ] `DELETE /api/select/{id}` - Stop following
- [ ] `GET /api/following` - List all followed fixtures
- [ ] `PATCH /api/following/{id}` - Update status/notes

### 7.3 Selection UI
- [ ] Add "Follow" button on prediction cards
- [ ] Add "Select for Betting" button on prediction cards
- [ ] Create "My Following" page showing selected fixtures
- [ ] Filter: All Predictions vs My Following

### 7.4 Event Integration
- [ ] `FixtureUpdated` → Update followed fixtures in real-time
- [ ] `OddsUpdated` → Recalculate EV for followed fixtures
- [ ] `FixtureCompleted` → Mark as settled, show result
- [ ] WebSocket push to connected clients

### 7.5 Multi-User Preparation
- [ ] Add user_id to all selection models (nullable for now)
- [ ] API accepts optional user_id (from session)
- [ ] Queries filter by user_id when present
- [ ] Tests: `tests/web/test_following.py`

### 7.6 Git Checkpoint
- [ ] Commit user following features
- [ ] Tag: `v1.1-following`

---

## TESTING STRATEGY

### Unit Tests (Run on every change)
```bash
pytest tests/events/ -v
pytest tests/handlers/ -v
pytest tests/cache/ -v
```

### Integration Tests (Run before commit)
```bash
pytest tests/ -v --integration
```

### Manual Test Checklist
- [ ] Emit FixtureCompleted → verify predictions settled
- [ ] Update odds → verify EV recalculated
- [ ] Check prediction page loads from cache
- [ ] Verify new market appears in dropdown
- [ ] Check drift detection triggers alert

### Performance Tests
- [ ] 100 fixtures: < 500ms load time
- [ ] Cache hit rate: > 90%
- [ ] Event processing: < 100ms latency

---

## GIT STRATEGY

### Commit Frequency
- After each phase completion
- After significant feature
- After bug fix with test

### Tagging
- `v0.1-event-base` - Phase 1 complete
- `v0.2-handlers` - Phase 2 complete
- `v0.3-cache` - Phase 3 complete
- `v0.4-markets` - Phase 4 complete
- `v0.5-training` - Phase 5 complete
- `v1.0-realtime` - Phase 6 complete
- `v1.1-following` - Phase 7 complete

### Rollback Plan
- Each tag is a deployable state
- `git checkout v0.1-event-base` to revert

---

## CURRENT PRIORITY

**NEXT**: Phase 7 - User Following & Manual Selection

**RATIONALE**:
- Real-time tracking requires knowing WHICH fixtures to follow
- Multi-user requires per-user fixture selection
- Manual betting override requires selection model
- Betting bot needs "select for betting" vs "just watch"

**IMMEDIATE TASKS**:
1. Create `WatchedFixture` model in `src/storage/models.py`
2. Create selection API endpoints
3. Add Follow button to prediction cards
4. Create "My Following" filter
5. Tests: `tests/web/test_following.py`
6. Commit with tag `v1.1-following`

---

## LESSONS LEARNED (Updated)

- [x] Events must be immutable
- [x] Handlers should not block event emission
- [x] Cache invalidation should be event-driven
- [x] Each market needs its own model/features
- [x] Commit before major refactors
- [x] Test incrementally, not at end
