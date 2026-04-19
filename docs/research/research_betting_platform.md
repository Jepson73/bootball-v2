# Betting Platform Research

## 1. Core Architecture

### Recommended Stack
- **Backend**: Python (Flask/FastAPI) or Go for high concurrency
- **Database**: PostgreSQL (user data, bets) + Redis (sessions, cache)
- **Real-time**: WebSocket for live odds/chat
- **Auth**: JWT + bcrypt password hashing

### Key Services (Microservices)
1. **User Service**: Authentication, profiles, KYC
2. **Betting Service**: Bet placement, validation, settlement
3. **Odds Engine**: Real-time odds fetching and processing
4. **Risk Service**: Fraud detection, limits
5. **Chat Service**: Messages, real-time
6. **Notification Service**: Alerts, follows

### Security Layers
- DDoS protection (Cloudflare)
- WAF (Web Application Firewall)
- Rate limiting (prevent brute force)
- Input sanitization
- HTTPS only
- CSRF tokens
- Audit logging

---

## 2. Expected Value (+EV) Betting

### The Math
```
EV = (Your Probability × Odds) - 1
```

**Positive EV** = You have an edge  
**Negative EV** = House has the edge

### Finding +EV Bets
1. **Build models** - Calculate true probability vs implied
2. **Line shop** - Compare odds across bookmakers
3. **Closing Line Value (CLV)** - Did your odds beat the close?
4. **Promotions** - Convert bonuses to +EV via hedging

### Professional Metrics
- Need 500-1000 bets minimum to validate
- Track: ROI, Win%, CLV
- +5% edge is excellent
- Beat closing line = sharp bettor

### Kelly Criterion
```
Stake % = (bp - q) / b

Where:
- p = your probability
- q = 1 - p  
- b = decimal odds - 1
- b = fraction of bankroll
```

**Tip**: Use fractional Kelly (25-50%) to survive variance

---

## 3. Community Features

### Leaderboards
Categories to track:
- ROI (Return on Investment)
- Win Rate %
- Profit $
- Bet Streaks
- CLV (closing line value)

### Social Features
- Follow top bettors
- Share bets to feed
- Comment/react on bets
- Head-to-head challenges
- Private leagues

### Gamification
- Daily/weekly resets
- Achievement badges
- Streak counters
- Tiered rewards (VIP levels)

### Betting Circles
- Friend groups
- Private competitions
- Group tracking

---

## 4. Database Design

### Core Tables

```sql
-- Users
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- User Sessions
CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    token VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

-- User Follows
CREATE TABLE user_follows (
    follower_id INTEGER REFERENCES users(id),
    following_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (follower_id, following_id)
);

-- User Bets
CREATE TABLE user_bets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    fixture_id INTEGER NOT NULL,
    market VARCHAR(20) NOT NULL,  -- h2h, btts, ou
    outcome VARCHAR(10) NOT NULL,    -- H, D, A or Y, N or O, U
    stake DECIMAL(10,2) NOT NULL,
    odds DECIMAL(6,2) NOT NULL,
    placed_at TIMESTAMP DEFAULT NOW(),
    settled BOOLEAN DEFAULT FALSE,
    result VARCHAR(10),           -- W, L, P
    profit DECIMAL(10,2)         -- positive or negative
);

-- User Flags (bet watching)
CREATE TABLE user_flags (
    user_id INTEGER REFERENCES users(id),
    fixture_id INTEGER NOT NULL,
    market VARCHAR(20) NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (user_id, fixture_id, market)
);

-- Messages
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER REFERENCES users(id),
    to_user_id INTEGER REFERENCES users(id),  -- NULL for public
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    is_read BOOLEAN DEFAULT FALSE
);

-- Achievements
CREATE TABLE achievements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    type VARCHAR(50) NOT NULL,
    earned_at TIMESTAMP DEFAULT NOW()
);
```

---

## 5. Multi-User Chat

### Features
- Direct messages (user to user)
- Chat rooms (public channels)
- Real-time (WebSocket)
- Read receipts
- Message moderation

### Implementation
- Store in messages table
- WebSocket for real-time
- Rate limit messages (prevent spam)
- Profanity filter
- Report/block functionality

---

## 6. Flagging System

### Purpose
- Track bets you want to watch
- Community signals ("🔥 3 users flagged")
- Top Flagged list

### Aggregation Query
```sql
SELECT fixture_id, market, COUNT(*) as flag_count
FROM user_flags
GROUP BY fixture_id, market
ORDER BY flag_count DESC
LIMIT 10;
```

### Display
- Show flag count on high-interest bets
- Filter: "Most Flagged" tab
- Alerts when flagged bet is starting

---

## 7. Security Implementation

### Authentication
- bcrypt/argon2 for passwords (NEVER plain)
- JWT tokens with expiry
- Secure cookies (httpOnly, secure, sameSite)
- Rate limiting on login

### CSRF Protection
- Generate token per session
- Include in forms
- Validate on submission

### Input Sanitization
- Parameterized queries (prevent SQL injection)
- Escape HTML (prevent XSS)
- Validate with schema libraries

### Audit Logging
- Log all bet placements
- Log logins/logouts
- Log admin actions

---

## 8. Sources & References

### Architecture
- BetForge Sportsbook Platform Guide
- AWS Sports Betting Architecture
- Stake.com architecture breakdown

### +EV Strategies (Mainstream)
- Sports Media 101: +EV Betting Guide
- WagerProof: Value Betting Strategies
- ProbWin: Value Betting Guide

### Advanced/Academic Research (Out of Box)
- [ArXiv 2303.06021](https://arxiv.org/abs/2303.06021): Calibration > Accuracy (+34.69% ROI)
- [ArXiv 2010.12508](https://arxiv.org/abs/2010.12508): Decorrelation from bookmaker models
- [ArXiv 2501.05873](https://arxiv.org/abs/2501.05873): Distribution forecasting over outcomes
- [ArXiv 2512.08591](https://arxiv.org/abs/2512.08591): Long-sequence LSTM (8+ seasons)
- [ArXiv 2303.16741](https://arxiv.org/abs/2303.16741): Graph attention networks for player interactions
- [ArXiv 1502.05886](https://arxiv.org/abs/1502.05886): Twitter sentiment +8% edge
- [ArXiv 2210.06327](https://arxiv.org/abs/2210.06327): Lineup-based prediction (42% return)
- [ArXiv 1910.03203](https://arxiv.org/abs/1910.03203): Serve strength key for tennis

### Community/Social
- BettorEdge: Social Features
- Yu-Kai Chou: Leaderboard Design
- PromoteProject: Gamification in Betting

### Security
- BetForge: Security & Compliance
- SportsFirst: Legal Betting Development

---

## 9. Implementation Priorities

### Phase 1: Core (MVP)
1. User registration/login
2. Bet placement (single market)
3. Basic leaderboard (ROI)

### Phase 2: Social
1. Follow system
2. Bet sharing to feed
3. Comments

### Phase 3: Engagement
1. Head-to-head challenges
2. Private leagues
3. Achievements

### Phase 4: Advanced
1. Multi-market support
2. Chat rooms
3. Advanced analytics

---

## 10. Advanced Edge Detection (Research-Backed)

### 10.1 Model Architecture Recommendations

Based on academic research:

**For Outcome Prediction**:
```
Input Features:
- Player-level stats (not just team aggregates)
- Historical sequence (8+ seasons for temporal models)
- Social sentiment signals (if available)
- Lineup confirmed vs expected

Architecture:
- LSTM for temporal dependencies (NBA, soccer)
- Graph Attention Networks for player interactions
- Ensemble of Poisson + ML models for soccer
```

**Key Insight from Research**:
- Feature choice has MINOR influence on prediction quality
- Using ALL match data with equal weighting outperforms selective features
- Calibration via isotonic regression > accuracy optimization

### 10.2 Probability Calibration Pipeline

```
Raw Model Output → Isotonic Regression → Calibrated Probabilities

Why: 
- Model may predict 70% but calibrated output is 55%
- A 55% prediction that wins 55% = profitable
- A 70% prediction that wins 50% = losing
```

### 10.3 Decorrelation Strategy

Instead of building a model to predict outcomes directly:
1. Build a model to predict what the BOOKMAKER predicts
2. Find residuals (differences between your view and market)
3. Bet when residuals are large AND your view differs from market direction

### 10.4 Inefficient Markets to Target

| Market | Why Inefficient |
|--------|-----------------|
| First 5 Innings (MLB) | Pitcher changes create slow adjustment |
| Early Season | Limited data, herd behavior |
| Low-profile leagues | Less bookmaker attention |
| Player Props | Individual variance not fully priced |
| Live Betting | Real-time inefficiencies from crowd behavior |

### 10.5 Data Sources for Edge

- **Football API** (Roanuz): Real-time match data, player stats, lineups
- **Twitter/X API**: Sentiment analysis before matches
- **xG data**: Expected goals (more predictive than actual goals)
- **ELO ratings**: Baseline for team strength
- **Social betting patterns**: Flag/follow ratios as crowd signals

---

## 11. Summary: The Asymmetry of Information

**The Problem**: Bookmakers are 80% efficient. Finding 20% edges is hard.

**The Research Insight**: Most researchers focus on accuracy. The winners focus on:
1. **Calibration** - Are your 60% predictions correct 60% of the time?
2. **Decorrelation** - Is your model wrong in different ways than the market?
3. **Distribution** - Can you predict the full distribution of outcomes?
4. **Individualization** - Do you see what team-level models miss?

**The Meta-Skill**: Not predicting winners, but predicting when the MARKET is wrong.
- Bookmakers optimize for accuracy, not calibration
- Public overbets popular teams, creating underdog value
- Early season lines are based on last year's data (lagging)
- Live betting overreacts to recent events

**Your Edge**: Information the market doesn't have, processed in ways the market doesn't process it.
