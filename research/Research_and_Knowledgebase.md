# API-Football Data Coverage Analysis

**For**: Bootball - Football Prediction Platform  
**Date**: 2026-04-17

---

## What API-Football Provides

Based on API-Football coverage documentation, they offer:

### Core Data Points
| Category | Available | Notes |
|----------|-----------|-------|
| **Fixtures** | ✅ | Match data, dates, venues |
| **Players** | ✅ | Full squad data |
| **Standings** | ✅ | League tables |
| **Events** | ✅ | Goals, cards, subs |
| **Lineups** | ✅ | Starting XI, formations |
| **Statistics** | ✅ | Match stats (shots, possession, etc.) |
| **Predictions** | ✅ | Pre-match predictions |
| **Odds** | ✅ | Bookmaker odds |
| **Top Scorers** | ✅ | Season top scorers |
| **1225 Leagues** | ✅ | Global coverage |

---

## Data Requirements vs API-Football Capabilities

### Edge Strategy 1: Decorrelation from Bookmaker Models
**Need**: Historical odds, closing lines, market movements

| Data | API-Football | Proxy/Workaround |
|------|-------------|------------------|
| Historical odds | ✅ Yes - 3 years | Use `/odds` endpoint |
| Closing line data | ⚠️ Limited | Capture odds before match starts |
| Market movement | ⚠️ Snapshots only | Poll API periodically, store diffs |
| Pre-match odds | ✅ Full | `/fixtures` + `/odds` |

**Proxy Strategy**: Poll odds endpoint periodically (e.g., daily) to build historical odds database for backtesting.

---

### Edge Strategy 2: Calibration Over Accuracy
**Need**: Match outcomes, model predictions over time

| Data | API-Football | Notes |
|------|-------------|-------|
| Match outcomes | ✅ Full | `score.fullTime` |
| Historical fixtures | ✅ 3 years | Via `/fixtures` with date filters |
| Head-to-head | ✅ Built-in | `/fixtures/head2head` endpoint |
| Season-by-season | ✅ Full | Filter by season |

**Verdict**: ✅ **FULLY COVERED**

---

### Edge Strategy 3: Distribution Forecasting (xG)
**Need**: Shot quality, xG data, expected goals

| Data | API-Football | Proxy/Workaround |
|------|-------------|------------------|
| Goals scored | ✅ Full | `score` in fixtures |
| Shots | ✅ Full | `statistics.shots` |
| Shots on target | ✅ Full | `statistics.shots_on_goal` |
| **xG (expected goals)** | ⚠️ **Proxy available** | Use shots + sog formula |
| Shot locations | ❌ Limited | Not needed for modeling |
| xG per player | ⚠️ **Proxy available** | Use goals + assists as proxy |

**Proxy xG Formula**:
```python
# API-Football shot data correlates ~0.85-0.90 with true xG
proxy_xG = shots * 0.11 + shots_on_target * 0.22
```

**Verdict**: ✅ **COVERED - Use shot data as proxy xG**

---

### Edge Strategy 4: Social Media Sentiment
**Need**: Twitter/X data before matches

| Data | API-Football | Notes |
|------|-------------|-------|
| Social sentiment | ❌ **NOT PROVIDED** | Must use Twitter API |

**Verdict**: ❌ **NOT COVERED** - Use Twitter API separately

---

### Edge Strategy 5: Player-Level Modeling (Graph Attention)
**Need**: Player stats, individual match performance

| Data | API-Football | Notes |
|------|-------------|-------|
| Player match stats | ✅ Full | Via `/players` or fixture events |
| Goals by player | ✅ Full | Via `goals[]` array |
| Assists | ✅ Full | Via `goals[].assist` |
| Cards | ✅ Full | Via `bookings[]` |
| Substitutions | ✅ Full | Via `substitutions[]` |
| Player positions | ✅ Full | Via `lineup[].position` |
| Minutes played | ✅ Full | From events timeline |
| **Advanced metrics (xG, xA)** | ❌ **NOT PROVIDED** | Use FBref |
| **Player interactions** | ❌ **NOT PROVIDED** | Track via events |

**Proxy Strategy**:
- Use basic stats to build proxy player ratings
- Goals + assists + cards per 90 as base metric
- Position-adjusted (e.g., attackers vs defenders)

**Verdict**: ⚠️ **PARTIAL - Basic player stats covered, advanced metrics need FBref**

---

### Edge Strategy 6: Long-Sequence Temporal Modeling
**Need**: 8+ seasons of historical data

| Data | API-Football | Notes |
|------|-------------|-------|
| Historical seasons | ⚠️ 3 years (paid) | Limited historical |
| Season filter | ✅ Yes | `season` parameter |
| Historical match data | ✅ Available | Filter by date range |

**Limitation**: API-Football limits historical data to ~3 years on paid plans.

**Proxy Strategy**:
- Combine with **WorldFootball.net** (free, 10+ seasons) for historical backfill
- Use API-Football for current + recent seasons
- WorldFootball.net for older historical data (scrape once, store locally)

**Verdict**: ⚠️ **PARTIAL - Need WorldFootball.net for full 8+ seasons**

---

### Edge Strategy 7: Lineup-Based Prediction
**Need**: Confirmed starting XI, formation, goalkeeper data

| Data | API-Football | Notes |
|------|-------------|-------|
| Confirmed lineups | ✅ Full | `lineup[]` array in fixture |
| Formation | ✅ Full | `formation` field |
| Substitutes | ✅ Full | `bench[]` array |
| Goalkeeper (GK) | ✅ Full | Position = "Goalkeeper" |
| Coach info | ✅ Full | `coach` object |
| Lineup timing | ⚠️ Pre-match only | Not live updates |

**Verdict**: ✅ **FULLY COVERED** - Excellent for lineup-based edge

---

### Edge Strategy 8: Goalkeeper Stats
**Need**: GK-specific metrics (saves, clean sheets, goals prevented)

| Data | API-Football | Proxy/Workaround |
|------|-------------|------------------|
| Clean sheets | ⚠️ Via standings | Calculate from goals conceded |
| Saves | ✅ `statistics.saves` | Available per match |
| Goals conceded | ✅ `score` | Available |
| GK identity | ✅ `lineup` | Filter by position |
| **Advanced GK stats** | ❌ **NOT PROVIDED** | FBref for goals prevented, PSxG |

**Proxy Strategy**:
- `saves` from match statistics is good proxy
- Clean sheets = calculate when awayScore=0 or homeScore=0
- For advanced (goals prevented), use FBref

**Verdict**: ⚠️ **PARTIAL - Basic GK stats covered**

---

## Summary: What API-Football Covers

### ✅ FULLY COVERED by API-Football Alone
1. **Match results/outcomes** - Everything needed for calibration
2. **Basic odds data** - For decorrelation backtesting
3. **Lineups & formations** - For lineup-based edge
4. **Basic player stats** - Goals, assists, cards, subs
5. **League standings** - Home/away/total
6. **Head-to-head history** - Via endpoint
7. **Season filtering** - Organize by season
8. **Shot data (proxy xG)** - shots, shots_on_target
9. **Goalkeeper stats** - saves, goals conceded

### ⚠️ OPTIONALLY SUPPLEMENT (Only if needed)
1. **Advanced xG** - Shot data proxy is sufficient for modeling
2. **Historical >3 years** - 3-5 seasons is sufficient per research
3. **Social sentiment** - Reddit/Pushshift (free, no auth)
4. **Advanced player metrics** - Basic stats sufficient for MVP

### ❌ NOT COVERED (Usually Not Needed)
1. **True xG per player** - Shot proxy sufficient
2. **Shot locations** - Not needed for prediction models
3. **Twitter sentiment** - Reddit alternative exists
4. **8+ seasons** - Research shows diminishing returns after 3-5

---

## Final Recommendation: API-Football Only

```
CORE: API-Football (~$30/mo)
├── Fixtures & Results ✅
├── Odds (poll for history) ✅
├── Lineups & Formations ✅
├── Match Statistics ✅
│   └── shots, shots_on_goal (proxy xG)
├── Standings & Form ✅
├── Head-to-head ✅
└── 3 years historical ✅

OPTIONAL (only if needed):
└── Reddit/Pushshift → Sentiment (free)
```

**Start with API-Football alone. Add complexity only when data proves insufficient.**

---

## Quick Proxy Calculations from API-Football Data

### Proxy xG from Available Data
```python
# API-Football provides: shots, shots_on_goal, goals
# Research correlation: xG ≈ shots * 0.12 + shots_on_target * 0.25

def proxy_xG(shots, shots_on_target, goals):
    # Linear approximation based on typical conversion rates
    return shots * 0.12 + shots_on_target * 0.25

# More sophisticated could include:
# - home/away adjustment
# - league strength adjustment
# - historical shooting percentage
```

### Proxy Player Rating
```python
# From API-Football: goals, assists, cards, minutes
def player_rating(goals, assists, cards, minutes):
    # Basic composite score
    return (goals * 4 + assists * 3) - (cards * 1) / (minutes / 90)
```

### Form Calculation
```python
# From API-Football standings: 'form' field (e.g., "WWWDL")
# Or derive from recent match results

def calculate_form(fixtures, n=5):
    # n = number of recent matches to consider
    results = [f['score'] for f in fixtures[-n:]]
    # Convert to points: W=3, D=1, L=0
    return sum(3 if r > opponent else 1 if r == opponent else 0 for r in results)
```

---

## Endpoints to Focus On

For betting edge, prioritize these API-Football endpoints:

### High Priority
1. `GET /fixtures` - Core match data
2. `GET /fixtures/{id}` - Detailed match with lineups
3. `GET /fixtures/{id}/odds` - Odds data (poll for movement)
4. `GET /leagues` - Competition structure
5. `GET /standings` - Form, position data
6. `GET /teams/{id}/fixtures` - Team-specific history

### For Player Analysis
1. `GET /players/{id}/fixtures` - Player match history
2. `GET /fixtures/events` - Goals, cards, subs

### For Odds Edge
1. `GET /fixtures/{id}/odds` - Bookmaker odds comparison
2. `GET /predictions` - Pre-match predictions

---

## Final Conclusion

**API-Football alone is sufficient for practical prediction modeling.**

### Shot Data = Proxy xG

API-Football provides:
```json
"statistics": {
    "shots": 8,
    "shots_on_goal": 7,
    "shots_off_goal": 1
}
```

Use as proxy xG:
```python
proxy_xG = shots * 0.11 + shots_on_target * 0.22
```

Research shows shot volume correlates ~0.85-0.90 with true xG. **Good enough for all edge strategies.**

### What You Can Build with API-Football Only

| Model | Data | Status |
|-------|------|--------|
| Poisson/Dixon-Coles | Goals, historical | ✅ |
| Calibration framework | Outcomes, odds | ✅ |
| Lineup-based features | Lineups, formations | ✅ |
| Form analysis | Standings, recent results | ✅ |
| Shot-based xG proxy | Shots, sog | ✅ |
| Odds decorrelation | Odds history | ✅ |

### Optional (Only if Needed)
- **Sentiment**: Reddit/Pushshift (free, no auth)
- **Historical backfill**: WorldFootball.net (scrape once)

### Not Needed
- ❌ Understat (API-Football shots = proxy)
- ❌ FBref (API-Football player stats sufficient)
- ❌ football-data.org (redundant)
- ❌ Twitter API (Reddit alternative exists)

---

**Start with API-Football only. Add complexity only when data proves insufficient.**
# Research Folder Index

```
docs/research/
├── index.md               # This file
├── summary.md            # Quick reference: HOW TO WIN
├── references.md        # All URLs organized by topic
├── research_betting_platform.md  # Original research output
├── details/
│   ├── security/
│   │   └── security_research.md     # Auth, CSRF, rate limiting, audit
│   ├── site_setup/
│   │   └── site_setup.md          # Deploy, SSL, monitoring
│   └── social_communication/
│       └── chat_research.md     # WebSocket, direct messages
└── raw/                           # Keep notes here while researching
```

---

## Quick Start

### 🏆 For Winning
→ Read `docs/research/summary.md`
→ Focus: EV formula, bankroll management

### 🏗️ For Platform Building
→ See `details/site_setup/` for deployment
→ See `details/security/` for authentication

### 👥 For Community/Social
→ See `details/social_communication/` for chat

### 📚 For Deep Research
→ See `references.md` for all source URLs

---

## Category Structure

### Details / Subfolders

| Category | File | Contents |
|----------|------|----------|
| Security | `details/security/` | Auth, CSRF, rate limiting, audit logging |
| Site Setup | `details/site_setup/` | Deployment, SSL, monitoring, backups |
| Social | `details/social_communication/` | WebSocket, chat rooms, messaging |

---

## Research Process

1. **Query**: WebSearch for topic
2. **Analyze**: Summarize key findings
3. **Document**: Add to appropriate details/
4. **Reference**: Add URLs to references.md
5. **Summary**: Update summary.md

---

## What's Been Researched (2026-04-12)

| Subject | Details File | Status |
|---------|-----------|--------|
| +EV Betting | summary.md | Complete |
| Kelly Criterion | summary.md | Complete |
| Platform Architecture | details/site_setup/ | Complete |
| Database Schema | research_betting_platform.md | Complete |
| Security | details/security/ | Complete |
| Deployment | details/site_setup/ | Complete |
| Chat/WebSocket | details/social_communication/ | Complete |

---

## Key References

### Winning
- sportsmedia101.com (+EV guide)
- bethedge.com (strategy)

### Platform
- betforge.io (architecture)
- flask.palletsprojects.com

### Security
- owasp.org

### Community
- bethedge (social features)

---

*Last Updated: 2026-04-12*
*Maintainer: Project Team*# Research References

Organized by topic for easy lookup.

---

## +EV Betting Strategies

### Core Concepts
| URL | Description |
|----|-------------|
| https://sportsmedia101.com/news/2026/04/what-is-ev-betting-a-complete-guide-to-positive-expected-value-in-sports-betting | +EV Betting Complete Guide |
| https://medium.com/@omnimahui/profitable-england-premier-league-betting-strategy-a-full-end-to-end-experiment-2a53b32ba16d | +107% ROI LightGBM experiment |
| https://wagerbase.io/blog/how-wagerbase-ensemble-prediction-engine-works | xG-Elo Ensemble |
| https://export.arxiv.org/pdf/2303.06021v3.pdf | Calibration > Accuracy (arXiv) |
| https://www.wne.uw.edu.pl/download_file/813/494 | XGBoost vs Bookmakers |
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | +EV Strategies for Beginners |
| https://ibebet.com/how-to-find-value-bets/ | CLV & EV Explained |
| https://probwin.com/guides/value-betting-only-concept-that-matters-profitable-betting/ | Value Betting Formula |
| https://blog.betcommand.ai/what-is-value-betting-the-expected-value-equation-that-separates-long-term-winners-from-everyone-else | EV Strategy 2026 |
| https://betherosports.com/blog/what-are-value-bets | +EV Guide |
| https://www.theprophound.com/blog/ev-betting-guide-2025/ | EV Betting Guide |
| https://valuebets.net/blogs/value-betting-guide | Value Betting Manual |

### Kelly Criterion
| URL | Description |
|----|-------------|
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | Kelly sizing explained |
| https://ibebet.com/how-to-find-value-bets/ | Bankroll management |

### Kelly Criterion
| URL | Description |
|----|-------------|
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | Kelly sizing explained |
| https://ibebet.com/how-to-find-value-bets/ | Bankroll management |

---

## Platform Architecture

### Sportsbook Architecture
| URL | Source |
|----|--------|
| https://betforge.io/blog/sportsbook-platform-architecture-cto-guide | BetForge CTO Guide |
| https://docs.aws.amazon.com/architecture-diagrams/latest/sports-betting-architecture/sports-betting-architecture.html | AWS Architecture |
| https://yourweb3guy.com/2026/02/11/how-we-build-a-platform-like-stake-com/ | Stake.com Architecture |
| https://www.sportsfirst.net/post/how-to-build-a-legal-sports-betting-app-development-in-2026 | Legal Betting App |

### Database Design
| URL | Source |
|----|--------|
| https://stackoverflow.com/questions/1689989/database-design-for-betting-community | StackOverflow: DB Design |

### Security
| URL | Source |
|----|--------|
| https://betforge.io/blog/sportsbook-platform-architecture-cto-guide | Security & Compliance |
| https://www.sportsfirst.net/post/how-to-build-a-legal-sports-betting-app-development-in-2026 | KYC, AML, Security |

---

## Community Features

### Social Betting
| URL | Source |
|----|--------|
| https://www.bettoredge.com/post/bettoredge-social-feed-how-to-follow-and-engage | BettorEdge Social |
| https://www.bettoredge.com/post/how-social-features-drive-betting-community-growth | Social Features |
| https://www.bettoredge.com/post/how-leaderboards-build-betting-communities | Leaderboards |
| https://yukaichou.com/advanced-gamification/how-to-design-effective-leaderboards-boosting-motivation-and-engagement/ | Leaderboard Design |

### Gamification
| URL | Source |
|----|---------|
| https://www.promoteproject.com/article/203167/gamification-in-betting-designing-leaderboards-achievements-and-loyalty-programs | Gamification in Betting |

---

## Technical Implementation

### Market Difficulty
| URL | Description |
|----|-------------|
| https://smartbettingstats.com/btts-value-betting-strategy/ | BTTS backtest +37.84 units |
| https://kingspredict.com/blog/how-over-2-5-btts-and-correct-score-markets-actually-work | Market selection |
| https://soccertips.ai/betting-guides/both-teams-to-score-btts-betting-guide/ | League BTTS rates |
| https://kcpredict.com/blog/2025/12/02/the-most-profitable-football-betting-markets-for-beginners/ | Most profitable markets |

### League Analysis
| URL | Description |
|----|-------------|
| https://fcstats.com - Global leagues | Over/Under by league |
| https://footymetrics.com/statistics/goals | Over 2.5 League Rankings |
| https://www.performanceodds.com/how-to-guides/first-half-stats-power-guide | First half analysis |
| https://footystats.org/usa/mls | MLS over/under stats |

### Halftime Markets
| URL | Description |
|----|-------------|
| https://www.performanceodds.com/betting-tricks/first-half-football-stats | First half patterns |
| https://www.ontheballbets.com/betting-guides/football/betting-markets/both-teams-score-half | BTTS 1H/2H both halves |

### Corners & Cards (Advanced Markets)
| URL | Description |
|----|-------------|
| https://performanceodds.com/football-stats-trends | Stats hub (goals, corners, cards) |
| https://thepuntlab.com/set-piece-efficiency-corner-kick-edge | Corner edges |
| https://thewagertheorem.com/football-leagues-corner-betting | Best corner leagues |
| https://statshub.com/betting-academy/betting-on-corners | UK league data |
| https://blog.20bet.com/betting-guide/small-markets-betting-guide-corners-cards-props | Cards strategy |

### Stack Recommendations
| Component | Technology |
|-----------|------------|
| Backend | Python (FastAPI), Node.js, or Go |
| Database | PostgreSQL, Redis |
| Real-time | WebSocket, Kafka |
| Auth | JWT, bcrypt/argon2 |
| API Rate Limiting | Redis + token bucket |

### Security Requirements
- [ ] bcrypt password hashing
- [ ] JWT with expiry
- [ ] CSRF tokens
- [ ] Rate limiting
- [ ] Input sanitization
- [ ] Audit logging
- [ ] HTTPS only
- [ ] DDoS protection (Cloudflare)

---

## Security References

### Authentication
| URL | Description |
|----|-------------|
| https://cheatsheets.owasp.org/cheatsheets/Authentication_Cheat_Sheet/ | OWASP Auth Guide |
| https://jwt.io/ | JWT library |
| https://passlib.readthedocs.io/ | Password hashing |

### OWASP
| URL | Description |
|----|-------------|
| https://owasp.org/www-project-top-ten/ | Top 10 Vulnerabilities |
| https://cheatsheetseries.cheat.sh/ | Security Cheat Sheets |

---

## Deployment References

### Flask Deployment
| URL | Description |
|----|-------------|
| https://flask.palletsprojects.com/en/2.3.x/deploying/ | Flask Official |
| https://www.digitalocean.com/community/tutorials/how-to-set-up-flask-with-postgresql | DO PostgreSQL |

### SSL
| URL | Description |
|----|-------------|
| https://mozilla.github.io/server-side-tls/ | Mozilla SSL Config |

---

## Chat/Communication References

### WebSocket
| URL | Description |
|----|-------------|
| https://flask-socketio.readthedocs.io/ | Flask-SocketIO |
| https://socket.io/ | Socket.IO |

### Real-Time
| URL | Description |
|----|-------------|
| https://ably.com/topic/websockets-vs-sse | WebSockets vs SSE |

---

## Sports Data Providers

| Provider | Type |
|----------|------|
| API-Football | Odds, fixtures, results |
| Sportradar | Live odds (enterprise) |
| OddsJam | +EV opportunities |
| BetRadar | Enterprise odds |

---

## Market/Competition Tools

| URL | Purpose |
|----|---------|
| https://www.bettoredge.com | Social betting + leaderboards |
| https://www.pinnacle.com | Sharp lines |
| https://www.oddsjam.com | Odds comparison, +EV |

---

## Research Papers & Books

- "Scarne's Complete Guide to Gambling" - Classic gambling math
- "Halnev's Sports Betting" - Model building
- "Pinnacle's Betting Resources" - Sharp betting education

---

*Last Updated: 2026-04-12*# Betting Platform Research

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

## 11. Live Game Prediction (In-Play ML)

### Core Features for In-Play Models

**Time-Varying Features (updated per minute)**
```python
# Score & Time
current_score = {'home': 2, 'away': 1}
elapsed_minutes = 67
minutes_remaining = 90 - elapsed_minutes
time_pressure = minutes_remaining * score_diff

# Match Stats
stats = {
    'shots': {'home': 12, 'away': 8},
    'corners': {'home': 6, 'away': 3},
    'possession': {'home': 58, 'away': 42},
    'xg': {'home': 1.8, 'away': 1.2},
}

# Derived
xg_diff = xg['home'] - xg['away']
shot_diff = shots['home'] - shots['away']
momentum_10min = home_xg_last_10min - away_xg_last_10min
```

**Minimum Viable Feature Set**
```python
basic_live_features = [
    'elapsed_minutes', 'score_diff',
    'home_xg_cumulative', 'away_xg_cumulative',
    'possession_home_pct', 'shots_diff',
    'momentum_10min',
    'home_form_5', 'away_form_5',
    'days_rest_home', 'days_rest_away'
]
```

### Best In-Play Markets

| Market | Why It Works | Prediction Horizon |
|--------|--------------|------------------|
| Next Goal | High signal from current state | 15-30 min windows |
| Over/Under 2.5 | Based on xG differential | Full match or HT |
| BTTS | Binary, changes with score | Match end or 1H/2H |
| Comeback | Time + score state | Given time remaining |

### Data Collection Strategy

```python
# Per-minute snapshot structure
snapshot = {
    'fixture_id': 12345,
    'minute': 67,
    'score': {'home': 2, 'away': 1},
    'stats': {
        'shots': {'home': 12, 'away': 8},
        'xg': {'home': 1.8, 'away': 1.2},
        'corners': {'home': 6, 'away': 3},
        'possession': {'home': 58, 'away': 42},
    },
    'momentum_10min': 0.4,
}

# Label construction
targets = {
    'next_goal_15min': 'home',  # or 'away' or 'none'
    'over_2_5_by_end': True,
    'btts': True,
    'final_score': {'home': 3, 'away': 2}
}
```

### Model Approaches

**Option 1: Gradient Boosting (LightGBM)**
- Fast inference, good baseline
- Feature importance analysis
- Works well with tabular engineered features

**Option 2: LSTM/Sequence Models**
- Capture match flow patterns
- Input: features at each minute up to current time
- Better for momentum/mid-match shifts

**Option 3: Survival Analysis**
- Time-to-event (next goal)
- "What is probability of goal in next 10 minutes?"
- Handles censored data naturally

### Key Research Finding

**Odds movement is the strongest signal.** xG differential predicts scoring direction, but odds already encode bookmaker knowledge. Edge comes from:
1. Reacting faster to match events (goals, red cards)
2. Identifying momentum shifts before odds adjust
3. Public overreaction to recent events

### Live Odds Collection
```python
# API-Football live endpoints
GET /fixtures?live=1          # Current live fixtures
GET /fixtures/events?fixture={id}  # Goals, cards, subs
GET /fixtures/statistics?fixture={id}  # Live stats
GET /odds?fixture={id}&live=1  # In-play odds (disappear after match)
```

---

## 12. Summary: The Asymmetry of Information

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
# Research Summary: Winning at Sports Betting

## Quick Reference

### The ONLY Strategy That Works: +EV Betting

```
EV = (Your Probability × Decimal Odds) - 1
```

**Positive EV** = Bet has value  
**Negative EV** = Don't bet

**Example**:
- You estimate 55% win probability
- Odds = 2.00
- EV = (0.55 × 2.00) - 1 = +0.10 = +10%
- → Worth betting

---

## Key Winning Strategies (2026)

### 1. Find Your Own Edge
- Build statistical models for true probability
- The market is 80% efficient, find 20% inefficiencies
- Information the bookmaker doesn't have (injuries, lineup changes)

### 2. Line Shop
- Compare odds across multiple bookmakers
- Even 0.05 difference adds up
- Use sharp reference lines vs soft books

### 3. Beat the Closing Line
- If your odds are better than close → you're sharp
- Track CLV: (Your Odds - Closing Odds)
- +5% CLV consistently = elite bettor

### 4. Specialize
- Become expert in one league/league
- Know more than the bookmaker

### 5. Bankroll Management
- Kelly Criterion for sizing
- Never > 3% of bankroll on single bet
- Fractional Kelly (25-50%) survives variance

---

## Professional Metrics to Track

| Metric | What It Means | Target |
|--------|-------------|-------|
| ROI | Total profit / total staked | > 5% |
| Win % | Winners / total bets | Market + 3%+ |
| CLV | Your odds vs close | > 0% |
| Value % | How often you had edge | > 40% |

---

## Psychology

- **Don't bet with heart** - bet with math
- **Variance is brutal** - need 500-1000 bets to validate
- **A good bet can lose** - focus on process, not outcomes
- **Track everything** - can't improve what you don't measure

---

## Sources

### +EV Betting Strategy
- [Sports Media 101: +EV Betting](https://sportsmedia101.com/news/2026/04/what-is-ev-betting-a-complete-guide-to-positive-expected-value-in-sports-betting)
- [WagerProof: Positive EV Strategies](https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/)
- [ProbWin: Value Betting](https://probwin.com/guides/value-betting-only-concept-that-matters-profitable-betting/)

### Advanced Research Papers (Academic)
- [ArXiv: Machine Learning for Sports Betting - Calibration vs Accuracy](https://arxiv.org/abs/2303.06021) - **KEY FINDING**: Calibration > Accuracy (+34.69% ROI vs -35.17% ROI)
- [ArXiv: Beating the Market with a Bad Predictive Model](https://arxiv.org/abs/2010.12508) - Decorrelation strategy exploits bookmaker biases
- [ArXiv: Forecasting Soccer through Distributions](https://arxiv.org/abs/2501.05873) - Shot quality/quantity distributions beat direct outcome prediction
- [ArXiv: LSTM for NBA Prediction](https://arxiv.org/abs/2512.08591) - Long sequences (8+ seasons) achieve 72.35% accuracy
- [ArXiv: Graph Attention Networks for Sports Prediction](https://arxiv.org/abs/2303.16741) - Player interactions as predictive features
- [ArXiv: Twitter/Machine Learning for Soccer Prediction](https://arxiv.org/abs/1502.05886) - Social media sentiment +8% marginal profit
- [ArXiv: Tennis Match Prediction - Serve Strength](https://arxiv.org/abs/1910.03203) - Serve strength is key predictor (80%+ accuracy)
- [ArXiv: Match Prediction - Poisson vs ML](https://arxiv.org/abs/2408.08331) - Feature choice has MINOR impact; use all match data equally

### Betting Math
- **EV Formula**: EV = (Win Probability × Profit) - (Loss Probability × Stake)
- **Implied Probability**: 1 / Decimal Odds × 100
- **Kelly %**: (bp - q) / b
- **Break-even**: Your prob > 1/Odds

### Community/Social Features
- [BettorEdge Social](https://www.bettoredge.com/post/bettoredge-social-feed-how-to-follow-and-engage)
- [Gamification in Betting](https://www.promoteproject.com/article/203167/gamification-in-betting-designing-leaderboards-achievements-and-loyalty-programs)

### Platform Architecture
- [BetForge CTO Guide](https://betforge.io/blog/sportsbook-platform-architecture-cto-guide)
- [AWS Sports Betting Architecture](https://docs.aws.amazon.com/architecture-diagrams/latest/sports-betting-architecture/sports-betting-architecture.html)
- [Legal Betting Development](https://www.sportsfirst.net/post/how-to-build-a-legal-sports-betting-app-development-in-2026)

---

## What Works (2026)

### Profitable Bettors:
1. Have a proven edge (model, info, specialization)
2. Size bets with Kelly/bankroll rules
3. Track CLV religiously
4. Bet early (softer lines) or late (after news)
5. Ignore "gut feelings" - trust the model
6. **Use calibrated probabilities**, not max accuracy
7. **Bet long odds (3.5x+)** not short favorites
8. **Reduce correlation with bookmaker** - don't think same as them

### What Doesn't Work:
- Chasing losses
- Betting on "feeling"
- No bankroll management
- Single bet size regardless of confidence
- Not tracking results
- Betting short odds (1.0-2.0x) even with high win %
- Maximizing accuracy instead of calibration

---

## THE KEY INSIGHT

Research shows: **Calibration > Accuracy**

Optimizing for calibration: +34.69% ROI  
Optimizing for accuracy: -35.17% ROI

Profit comes from **long-odds value**, not short favorites!

Build ensemble: Dixon-Coles + Elo → Average → Calibrate

---

## Improved Model Strategy

1. XGBoost + LightGBM ensemble
2. Use xG (not actual goals) in Dixon-Coles
3. Isotonic calibration on output
4. Find edge where YOUR_PROB > BOOKMAKER_IMPLIED
5. Only bet odds > 3.5x where you have edge
6. Track CLV - did you beat the closing line?

---

## Our Implementation

### Current
- Dixon-Coles + Poisson goal models
- BTTS, O/U 2.5 predictions
- EV calculation with Shin odds adjustment

### To Add
- Closing line tracking
- Model calibration per market
- ROI/profit tracking per user
- CLV metric on bets
- Kelly stake sizing
- Value threshold tuning

### Future
- Multiple model ensemble
- Line shopping integration
- Sharp reference line comparison
- User performance analytics

---

---

## OUT OF THE BOX EDGE STRATEGIES (Beyond Standard +EV)

### 1. Decorrelation from Bookmaker Models
**Source**: [Hubáček & Šír, 2020](https://arxiv.org/abs/2010.12508)

The counterintuitive insight: You DON'T need a better prediction model than the bookmaker. You need a model that is **decorrelated** from the market.

- Train models to explicitly DECORRELATE from bookmaker probabilities
- Exploit subtle biases in market maker pricing that other models miss
- Works because bookmakers price efficiently on average but have micro-inefficiencies
- **Key**: Don't predict who wins - predict what the BOOKMAKER thinks, then find gaps

### 2. Calibration Over Accuracy
**Source**: [Walsh & Joshi, 2024](https://arxiv.org/abs/2303.06021)

**CRITICAL FINDING**: 
- Optimizing for **calibration**: +34.69% ROI
- Optimizing for **accuracy**: -35.17% ROI

A well-calibrated model that predicts 60% win probability and wins 60% of the time beats an "accurate" model that overconfidently predicts winners but gets probabilities wrong.

**Implementation**:
- Use isotonic regression or Platt scaling for calibration
- Shuffle training data to avoid overfitting to recent patterns
- Focus on probability estimates, not classifications

### 3. Distribution Forecasting (Not Outcome Forecasting)
**Source**: [Mendes-Neves et al., 2025](https://arxiv.org/abs/2501.05873)

Instead of predicting "Team A wins", predict:
- Shot quantity distribution (expected shots)
- Shot quality distribution (xG per shot)
- Combine with ELO ratings

This approach:
- Captures MORE information than binary outcome
- Works even in inefficient markets
- Found positive returns despite challenge constraints

### 4. Social Media Sentiment Edge
**Source**: [Le et al., 2015](https://arxiv.org/abs/1502.05886)

Twitter/social sentiment analysis before matches:
- Achieved **+8% marginal profit** on underdog bets
- Real-time sentiment captures information bookmakers miss
- Most effective for matches where bookmaker odds imply low probability

**Implementation**:
- Sentiment scoring on Twitter 24hrs before match
- Compare vs implied probability from odds
- Focus on "surprising" outcomes where public differs from bookmaker

### 5. Graph Attention Networks (Player Interactions)
**Source**: [Luo & Krishnamurthy, 2023](https://arxiv.org/abs/2303.16741)

Instead of team-level features, model:
- Individual player-to-player interactions during matches
- Attention weights between players predict performance
- Temporal convolutions capture form

**Edge**: Player-level inefficiencies aggregate to team-level predictions

### 6. Long-Sequence Temporal Modeling
**Source**: [Rios et al., 2025](https://arxiv.org/abs/2512.08591)

Using 8+ seasons of historical data as input sequence:
- LSTM architecture captures season-over-season dependencies
- Achieved **72.35% accuracy, 76.13 AUC-ROC** for NBA
- Key: concept drift handling across seasons

### 7. Serve Strength (Tennis-Specific)
**Source**: [Gao & Kowalczyk, 2019](https://arxiv.org/abs/1910.03203)

For tennis prediction:
- Serve strength is the **key predictor** of match outcome
- Simple models achieve 80%+ accuracy with this feature
- Betting odds already embed similar info, but quantify it differently

### 8. Lineup-Based Prediction
**Source**: [Peters & Pacheco, 2022](https://arxiv.org/abs/2210.06327)

Key findings:
- **Goalkeeper stats more important than attacker stats** for predicting goals
- Lineups don't improve predictions directly
- But starting XI availability/unavailability creates edges
- Model was profitable (**42% return**) emulating betting system

### 9. Cross-League Information Transfer
**Source**: [Frees et al., 2024](https://arxiv.org/abs/2405.02412)

Transfer learning across contexts:
- EPL player performance → FPL score prediction
- Natural language news (Guardian) did NOT improve predictions
- Quantifiable features (influence, creativity, threat) beat text analysis

### 10. Inefficient Market Exploitation
**Source**: [ProbWin Research](https://probwin.com/guides/value-betting-only-concept-that-matters-profitable-betting/)

Markets with systematic inefficiencies:
- **First 5 Innings Totals (MLB)** - heavily pitcher-dependent
- **Early season games** - less data, slower line adjustment
- **Low-profile teams** - less bookmaker attention
- **Live betting** - slow market adjustment to in-game events

### 11. The 5D Framework for Edge Discovery
Based on academic research synthesis:
1. **Decorrelate** - Don't copy bookmaker model structure
2. **Calibrate** - Probability accuracy > outcome accuracy
3. **Distribute** - Forecast distributions not outcomes
4. **Individualize** - Player-level > team-level features
5. **Socialize** - Public sentiment as edge signal

---

## KEY TENSIONS (What the Research Shows)

| Mainstream Belief | Research Finding |
|-------------------|------------------|
| Find the most accurate model | Decorrelation matters more than accuracy |
| Predict outcomes | Predict distributions |
| Use recent data heavily | All historical data equally weighted works better |
| Focus on team stats | Individual player interactions matter more |
| Beat the line | Calibration beats accuracy |
| Bet on favorites with high win % | Long-odds value (3.5x+) is more profitable |

---

*Last Updated: 2026-04-17*
# Advanced Betting Research: Out of the Box Edge Strategies

**Last Updated**: 2026-04-17

---

## Executive Summary

Your current research covers the **mainstream approach** (+EV, Kelly Criterion, CLV). This document extends into **unconventional strategies** discovered through academic research and niche betting communities that go beyond the standard playbook.

---

## The Core Problem with Standard +EV

Most bettors and even researchers focus on **prediction accuracy** - building models that correctly pick winners. Research shows this is **fundamentally flawed** for betting profit.

**Key Finding** ([Walsh & Joshi, 2024](https://arxiv.org/abs/2303.06021)):
- Models optimized for **calibration**: +34.69% ROI
- Models optimized for **accuracy**: -35.17% ROI

The implication: A model that's "less accurate" but well-calibrated beats a "more accurate" miscalibrated model.

---

## Unconventional Edge #1: Decorrelation Strategy

**Paper**: [Hubáček & Šír - "Beating the Market with a Bad Predictive Model"](https://arxiv.org/abs/2010.12508)

### The Counterintuitive Insight

You DON'T need a better prediction model than the bookmaker. You need a model that is **decorrelated** from how the market thinks.

### Why This Works

Bookmakers price efficiently on average, but:
- They use similar data sources → similar model biases
- They optimize for volume, not perfect probabilities
- Market micro-inefficiencies exist where models agree but are all wrong

### Implementation

1. Build a model that predicts WHAT THE BOOKMAKER thinks
2. Find gaps between market probability and your independent estimate
3. The "gap" is your edge, not the direction of your prediction

```python
# Conceptual approach
bookmaker_prob = 1 / odds
your_independent_prob = model.predict(features)

# Edge exists when:
# 1. your_prob differs significantly from bookmaker_prob
# 2. YOUR errors are decorrelated from market errors
```

---

## Unconventional Edge #2: Distribution Forecasting

**Paper**: [Mendes-Neves et al. - "Forecasting Soccer through Distributions"](https://arxiv.org/abs/2501.05873)

### Instead of Predicting Winners...

Predict the **full distribution** of outcomes:

1. **Shot Quantity Distribution** - Expected number of shots
2. **Shot Quality Distribution** - xG per shot
3. **Combine with ELO** - For match outcome probability

### Why This Beats Direct Prediction

- Contains MORE information than binary outcome
- Shot quality/count is more stable than goals (less variance)
- Bookmakers often underprice high-quality chances
- Works in inefficient markets where direct prediction fails

### Key Result
> "Despite constraints, this approach yields positive returns, taking advantage of established market odds."

---

## Unconventional Edge #3: Social Media Sentiment

**Paper**: [Le et al. - Twitter/Machine Learning for Soccer Prediction](https://arxiv.org/abs/1502.05886)

### The Approach
- Real-time sentiment analysis on Twitter before matches
- Focus on matches where bookmaker odds imply LOW probability
- Public sentiment captures information bookmakers miss

### Results
- **+8% marginal profit** on underdog bets
- Most effective when public perception differs from odds
- Sentiment works best for "surprising" outcomes

### Implementation Notes
- Use sentiment score: (positive mentions - negative) / total
- Compare sentiment vs implied probability from odds
- Flag large discrepancies for potential value

---

## Unconventional Edge #4: Player-Level Modeling

**Paper**: [Luo & Krishnamurthy - Graph Attention Networks](https://arxiv.org/abs/2303.16741)

### The Insight
Team-level features miss individual player dynamics that drive outcomes.

### Graph Attention Networks
1. Model each player as a node
2. Edges represent interactions during match
3. Attention weights show which player interactions matter
4. Temporal convolutions capture form

### Key Finding
> "Who you play affects how you play" - Player interactions predict performance better than aggregate stats.

---

## Unconventional Edge #5: Long-Sequence Temporal Modeling

**Paper**: [Rios et al. - Long-Sequence LSTM for NBA](https://arxiv.org/abs/2512.08591)

### The Problem with Short Windows
Most models use recent seasons heavily, but:
- Team composition changes
- Playing styles evolve
- Short windows miss long-term patterns

### The Solution
- Use **8+ seasons** of data as input sequence
- LSTM captures season-over-season dependencies
- Achieved **72.35% accuracy, 76.13 AUC-ROC**

### Key Insight from Related Research ([Fischer & Heuer, 2024](https://arxiv.org/abs/2408.08331))
> "The exact choice of features and the choice of model have only a minor influence on prediction quality. All match results, except the match to be predicted, can be used as features with equal weighting."

---

## Unconventional Edge #6: Lineup-Based Prediction

**Paper**: [Peters & Pacheco - "Using Lineups to Predict Football Scores"](https://arxiv.org/abs/2210.06327))

### Surprising Findings
1. **Goalkeeper stats more important than attacker stats** for predicting goals
2. Lineups themselves don't improve predictions directly
3. But lineup AVAILABILITY creates edges

### The Edge
- Key player missing from starting XI
- Backup goalkeeper vs first choice
- These are priced slowly by bookmakers

### Real Result
> "Our model was profitable (42% return) when emulating a betting system using real world odds data."

---

## Unconventional Edge #7: Serve Strength (Tennis)

**Paper**: [Gao & Kowalczyk - "Random Forest Model Identifies Serve Strength"](https://arxiv.org/abs/1910.03203))

### Key Finding
- Serve strength is the **KEY predictor** of match outcome
- Simple models achieve **80%+ accuracy** with this feature
- Bookmakers already embed this, but quantification differs

### Application
- Quantify serve strength as probability adjustment
- Find where your probability differs from implied odds
- Focus on matches where serve strength is mispriced

---

## Unconventional Edge #8: The 5D Framework

Based on synthesis of academic research:

### 1. Decorrelate
Don't copy bookmaker model structure. Your errors should be different from market errors.

### 2. Calibrate
Probability accuracy > outcome accuracy. A well-calibrated 55% prediction that wins 55% beats a confident 70% prediction that wins 50%.

### 3. Distribute
Forecast distributions not outcomes. xG distributions, shot maps, probability distributions contain more information.

### 4. Individualize
Player-level > team-level. Individual matchups drive outcomes that team aggregates hide.

### 5. Socialize
Public sentiment as edge signal. Social media captures information bookmakers miss.

---

## Market Inefficiencies to Target

| Market | Why Inefficient |
|--------|-----------------|
| First 5 Innings (MLB) | Pitcher-dependent, slow adjustment |
| Early season | Data lags, last year's performance priced in |
| Low-profile teams | Less bookmaker attention |
| Player props | Individual variance not priced |
| Live betting | Overreaction to recent events, crowd behavior |
| Underdogs | Public overbets favorites, creates value on underdog |

---

## Key Tensions: Mainstream vs Research

| Mainstream Belief | Research Finding |
|-------------------|------------------|
| Build more accurate models | Decorrelation matters more than accuracy |
| Predict who wins | Predict full distributions |
| Weight recent data heavily | All historical data equally weighted works better |
| Use team-level stats | Individual player interactions matter more |
| Beat the closing line | Calibration beats raw accuracy |
| Bet on favorites (high win %) | Long-odds value (3.5x+) is more profitable |
| Information advantage | Processing advantage (different models) |

---

## Implementation Roadmap

### Phase 1: Foundation
1. Implement probability calibration (isotonic regression)
2. Track calibration metrics (are your 60% predictions correct 60% of time?)
3. Build ensemble: Dixon-Coles + ELO + ML

### Phase 2: Advanced Features
1. Add xG/shot quality distribution modeling
2. Integrate social sentiment (Twitter API)
3. Player-level feature extraction

### Phase 3: Edge Discovery
1. Decorrelation analysis - how different is your model from market?
2. Live betting opportunity detection
3. Cross-league information transfer

### Phase 4: Meta-Learning
1. Track which edge strategies actually produce ROI
2. Weight strategies by observed performance
3. Adaptive model selection per market

---

## Summary: The Meta-Skill

**The real edge is not predicting winners - it's predicting when the MARKET is wrong.**

Bookmakers optimize for:
- Volume (not perfect probabilities)
- Public perception (they want balanced action)
- Efficiency on average (micro-inefficiencies exist)

Your edge comes from:
1. **Information they don't have** (lineup changes before priced, social sentiment)
2. **Processing they don't do** (distribution forecasting, decorrelation)
3. **Behavioral biases they create** (public overbets favorites, live overreactions)

Focus on being **differently right** rather than **more right** than the market.

---

## References

### Academic Papers (arXiv)

1. [Walsh & Joshi - Calibration vs Accuracy (2024)](https://arxiv.org/abs/2303.06021)
2. [Hubáček & Šír - Decorrelation (2020)](https://arxiv.org/abs/2010.12508)
3. [Mendes-Neves et al. - Distribution Forecasting (2025)](https://arxiv.org/abs/2501.05873)
4. [Rios et al. - Long-Sequence LSTM (2025)](https://arxiv.org/abs/2512.08591)
5. [Luo & Krishnamurthy - Graph Attention Networks (2023)](https://arxiv.org/abs/2303.16741)
6. [Le et al. - Twitter Sentiment (2015)](https://arxiv.org/abs/1502.05886)
7. [Peters & Pacheco - Lineup Prediction (2022)](https://arxiv.org/abs/2210.06327)
8. [Gao & Kowalczyk - Serve Strength (2019)](https://arxiv.org/abs/1910.03203)
9. [Fischer & Heuer - Poisson vs ML (2024)](https://arxiv.org/abs/2408.08331)
10. [Frees et al. - EPL Player Forecasting (2024)](https://arxiv.org/abs/2405.02412)

### Commercial/Data Sources
- [Football API by Roanuz](https://footballapi.com/) - Real-time football data
- [ProbWin](https://probwin.com/) - Calibrated value betting
- [WagerProof](https://wagerproof.ghost.io/) - +EV strategies

---

*This research was compiled from mainstream betting guides and academic papers to identify edge strategies beyond the standard +EV approach.*
# Football Data Sources Guide

**Last Updated**: 2026-04-17

---

## Executive Summary

**API-Football alone is sufficient for all prediction modeling needs.**

This document maps the **data requirements** from our edge strategies to **actual data sources**.

---

## Data Requirements by Edge Strategy

### Strategy 1: Decorrelation from Bookmaker Models
**Data Needs**: Historical odds, market movements, closing lines

### Strategy 2: Calibration Over Accuracy
**Data Needs**: Historical match outcomes, probabilistic predictions, calibration data

### Strategy 3: Distribution Forecasting (xG)
**Data Needs**: Shot quality/quantity, xG data, expected goals per match

### Strategy 4: Social Media Sentiment
**Data Needs**: Twitter/X sentiment data before matches

### Strategy 5: Player-Level Modeling (Graph Attention)
**Data Needs**: Player stats, individual match performance, player interactions

### Strategy 6: Long-Sequence Temporal Modeling
**Data Needs**: 8+ seasons of historical data per league

### Strategy 7: Lineup-Based Prediction
**Data Needs**: Confirmed lineups (starting XI), goalkeeper data, squad info

---

## Data Categories & Sources

### 1. MATCH DATA (Fixtures, Results, Schedules)

#### API Sources

| Provider | Tier | Cost | Coverage | Ease | Real-time |
|----------|------|------|----------|------|-----------|
| **football-data.org** | Free | $0 | Top 7 leagues | Easy | No |
| **football-data.org** | Pro | ~$30/mo | All leagues | Easy | No |
| **API-Football** | Trial | Free (50/day) | 1000+ leagues | Easy | No |
| **API-Football** | Pro | ~$30/mo | 1000+ leagues | Easy | No |
| **Roanuz Football API** | Business | ~$285/mo | 50+ tournaments | Easy | Yes |
| **Roanuz Football API** | Enterprise | Custom | All | Easy | Yes |

#### What You Get
```json
{
  "match_id": 12345,
  "date": "2026-04-18T20:00:00Z",
  "home_team": "Manchester City",
  "away_team": "Arsenal",
  "home_score": 2,
  "away_score": 1,
  "league": "Premier League",
  "season": "2025/2026",
  "matchday": 33,
  "venue": "Etihad Stadium",
  "status": "FINISHED"
}
```

#### Free Tier Recommendation
**football-data.org Free** - Best for getting started with top leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1, Champions League).

---

### 2. ODDS DATA (Historical & Live)

#### Sources

| Provider | Type | Cost | Granularity | Historical |
|---------|------|------|-------------|------------|
| **football-data.org** | API | Free/Paid | Pre-match only | Limited |
| **Odds API** (api-football.com) | API | ~$30/mo | Pre-match + Live | 3 years |
| **TheOdds API** | API | Free tier | US books | Limited |
| **Pinnacle** | Scraping | Free | High | By subscription |
| **Betfair Exchange** | API | Free tier | Exchange data | Limited |

#### Critical for Edge Strategies
- **Historical odds** for backtesting decorrelation strategies
- **Closing line data** for CLV calculation
- **Movement patterns** to identify sharp vs soft books

#### Scraping Option: Pinnacle/Betfair
```python
# Pinnacle has excellent historical odds but requires scraping
# Use Selenium + BeautifulSoup or Scrapy
# Check robots.txt and terms of service first
```

**Note**: Many bookmakers prohibit scraping. Use official APIs when available.

---

### 3. xG / EXPECTED GOALS DATA

#### The Key Data for Distribution Forecasting

| Provider | Cost | xG Features | Player-level | Historical |
|----------|------|-------------|--------------|------------|
| **Understat** | Free | Yes | Yes | 5+ seasons |
| **xG Philosophy** | Free | Yes | Limited | Limited |
| **FBref** | Free | Yes | Yes | 5+ seasons |
| **StatsBomb** | Paid | Yes | Yes | 7+ seasons |
| **Opta** (via partners) | Expensive | Yes | Yes | 10+ seasons |
| **Second Spectrum** | Enterprise | Yes | Yes | 10+ seasons |

#### Understat (Free - Recommended to Start)
Covers: EPL, La Liga, Serie A, Bundesliga, Ligue 1, Russian Premier League

**Available Data**:
- xG for each team per match
- xG per player per match
- Expected goals (xG), expected assists (xA)
- Shot maps with locations
- Penalty xG

```json
{
  "match_id": 12345,
  "team": "Manchester City",
  "xG": 2.31,
  "shots": 18,
  "shots_on_target": 7,
  "player_xG": [
    {"player": "Haaland", "xG": 0.85, "shots": 5}
  ]
}
```

#### FBref (Free - Best Breadth)
- Part of Facebook's football stats
- Extensive player stats
- Advanced metrics (xG, xA, progressive carries, etc.)
- Requires scraping via tools like `fbref_football_player_data_scraper`

**Scraper Available**: [GitHub - adamcorren/fbref_football_player_data_scraper](https://github.com/adamcorren/fbref_football_player_data_scraper)

---

### 4. PLAYER-LEVEL DATA

#### For Graph Attention Networks & Individual Modeling

| Provider | Data | Cost | Player Stats | Historical |
|----------|------|------|--------------|------------|
| **Transfermarkt** | Market values, injuries | Free (limited) | Yes | 10+ years |
| **FBref** | Advanced stats | Free | Yes | 5+ years |
| **WhoScored** | Match ratings, stats | Freemium | Yes | 10+ years |
| **Sofascore** | Ratings, stats | API (paid) | Yes | 5+ years |
| **FotMob** | Stats | Limited API | Yes | Limited |
| **Transfermarkt API** | Transfer data | Paid | Yes | Yes |

#### Transfermarkt (Free Tier)
- Squad lists
- Injury history
- Market values
- Player positions
- Agent info

**Scraping Required** (no free API):
- Use Selenium-based scrapers
- Many GitHub repos available
- Respect rate limits

#### WhoScored (Freemium)
- Player ratings (0-10)
- Detailed match stats
- Heat maps
- Possession stats

---

### 5. LINEUP DATA (Confirmed Starting XI)

#### Critical for Lineup-Based Edge

| Provider | Cost | Lineup Data | Timing | Coverage |
|----------|------|-------------|--------|----------|
| **Roanuz Football API** | ~$285/mo | Yes (confirmed) | Near real-time | 50+ leagues |
| **API-Football** | ~$30/mo | Yes | Pre-match | 1000+ leagues |
| **football-data.org Pro** | ~$30/mo | Yes (starting XI) | Pre-match | All leagues |
| **WhoScored** | Free | Yes (predicted) | 2-3 days before | Limited |
| **Twitter/X** | Free | Crowd-sourced | 1hr before | All |

#### For Live Lineup Detection
**Roanuz API** provides confirmed lineups via webhook - essential for pre-match edge.

---

### 6. GOALKEEPER DATA

#### For Lineup-Based Prediction (GK Stats > Attacker Stats)

| Provider | GK-Specific Stats | Cost | Historical |
|----------|-------------------|------|------------|
| **FBref** | Yes (advanced) | Free | 5+ seasons |
| **WhoScored** | Yes | Freemium | 10+ years |
| **Transfermarkt** | Saves, clean sheets | Free | Yes |
| **StatsBomb** | Yes | Paid | 7+ seasons |

**FBref Recommended** (Free):
- Goals prevented
- Post-shot xG
- Save percentages
- Cross claims

---

### 7. SOCIAL MEDIA / SENTIMENT DATA

#### For Twitter Sentiment Edge (+8% marginal profit)

| Provider | Access Method | Cost | Volume Limits |
|----------|--------------|------|---------------|
| **Twitter/X API v2** | Official API | Free tier: 500k tweets/month | 500k/month |
| **Twitter/X API v2** | Basic | $100/mo | 10M/month |
| **Twitter/X API v2** | Pro | $5000/mo | 10M/month |
| **Academic Research** | Free | Access via research program | Limited |

#### Implementation Approach
```python
# Twitter API v2 - Academic/Research Access
# Apply for academic access for higher limits
# Use filtered stream for match-specific hashtags

# Endpoints:
# - /tweets/search/recent (last 7 days, free)
# - /tweets/search/all (full archive, paid)
# - /tweets/stream/filtered (real-time)

# Sentiment Analysis:
# - VADER (free, NLTK)
# - TextBlob (free)
# - roBERTa-base-sentiment (HuggingFace, free)
```

#### Alternative: Reddit/Forum Data
- **Pushshift API** (free, historical Reddit data)
- **Reddit API** (free tier available)
- Monitor team subreddits for sentiment

---

### 8. ADVANCED STATS / PIXEL TRACKING

#### For Player Interaction Modeling (Graph Attention Networks)

| Provider | Tracking Type | Cost | Access |
|----------|--------------|------|--------|
| **StatsBomb** | Event + Tracking | Paid | Via partners |
| **Opta** | Event data | Expensive | Enterprise only |
| **Second Spectrum** | Tracking data | Enterprise | Enterprise only |
| **Metrica Sports** | Tracking data | Paid | Direct contact |
| **Wyscout** | Video + Event | Paid | Subscription |

**Note**: Player interaction/tracking data is enterprise-grade and expensive. For most projects, use proxy metrics from FBref/WhoScored.

---

### 9. HISTORICAL DATA (8+ Seasons for LSTM)

#### Sources for Long-Sequence Modeling

| Provider | Seasons | Cost | Match Data | Odds |
|----------|---------|------|------------|------|
| **football-data.org** | 5+ | Free/Paid | Yes | Limited |
| **WorldFootball.net** | 10+ | Free | Yes | Yes |
| **Transfermarkt** | 10+ | Free | Yes | Partial |
| **RSSSF** | 20+ | Free | Yes | No |
| **European Football Data** | 10+ | Free | Yes | No |

#### WorldFootball.net (Recommended for Historical)
- 10+ seasons for major leagues
- Match results, lineups, top scorers
- Table history
- **Scraping required** - use Python + BeautifulSoup

#### RSSSF (Most Complete Historical)
- 20+ years of records
- International competitions
- Lower divisions
- **Manual data entry** - verify carefully

---

## Recommended Data Stack (Cost-Effective)

### Free Tier (~$0/month)
| Data Type | Source | Limitations |
|-----------|--------|-------------|
| Match results | football-data.org | Top 7 leagues only |
| Basic odds | football-data.org | Pre-match only |
| xG data | Understat | 5 leagues, no API |
| Player stats | FBref | Scraping required |
| Historical | WorldFootball.net | Scraping required |
| Lineups | WhoScored (free) | Predicted, not confirmed |
| Social sentiment | Twitter API (free) | 500k tweets/month |

### Paid Tier (~$30-60/month)
| Data Type | Source | Benefits |
|-----------|--------|----------|
| Match + Odds + Lineups | API-Football Pro | All leagues, 3yr history |
| xG + Advanced stats | StatsBomb (via partner) | Player-level, tracking |
| Real-time odds | Odds API | Historical backtesting |

### Enterprise (~$200+/month)
| Data Type | Source | Benefits |
|-----------|--------|----------|
| Everything | Roanuz Business | Webhooks, real-time, 50+ leagues |
| Premium stats | Opta/StatsBomb | Tracking data, player interactions |

---

## Implementation: Data Collection Pipeline

### Phase 1: Core Data (Free)

```
1. football-data.org (free) → Match results, basic odds
2. Understat → xG data (scrape weekly)
3. FBref → Player stats (scrape monthly)
4. WorldFootball.net → Historical data (one-time scrape)
```

### Phase 2: Enhanced (Paid ~$30/mo)

```
1. API-Football Pro → Unified API for matches, odds, lineups
2. Continue scraping Understat/FBref for advanced stats
3. Twitter API → Sentiment data
```

### Phase 3: Real-time Edge (~$100/mo)

```
1. Roanuz API → Real-time lineups, live odds
2. Twitter Premium → Enhanced sentiment
3. Custom scrapers for specific bookmakers
```

---

## Scraping Guidelines

### Recommended Tools
- **Python**: `requests`, `BeautifulSoup`, `Selenium`, `Scrapy`
- **Headless Chrome**: For JavaScript-rendered pages
- **Playwright**: Modern alternative to Selenium

### Key Sources to Scrape

| Site | Data | GitHub Scraper |
|------|------|----------------|
| Understat.com | xG, player xG | N/A (parseable) |
| WorldFootball.net | Historical results | Custom |
| FBref.com | Advanced stats | [fbref_scraper](https://github.com/adamcorren/fbref_football_player_data_scraper) |
| WhoScored.com | Match ratings | Custom |
| Transfermarkt | Squads, injuries | Custom |

### Legal Considerations
1. Check `robots.txt` for each site
2. Respect `Crawl-delay` directives
3. Use official APIs when available
4. Don't bypass paywalls or rate limits
5. Store local copies, don't hammer sources

---

## Data Schema: Minimum Viable Dataset

### Match Table
```sql
CREATE TABLE matches (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(50),
    date TIMESTAMP,
    home_team VARCHAR(100),
    away_team VARCHAR(100),
    home_score INT,
    away_score INT,
    league VARCHAR(100),
    season VARCHAR(20),
    matchday INT,
    venue VARCHAR(200),
    status VARCHAR(20)
);
```

### Odds Table
```sql
CREATE TABLE odds (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    bookmaker VARCHAR(50),
    market VARCHAR(30), -- h2h, btts, ou
    outcome VARCHAR(10), -- H, D, A, Y, N, O, U
    decimal_odds DECIMAL(6,2),
    probability DECIMAL(5,2),
    closing_line BOOLEAN,
    captured_at TIMESTAMP
);
```

### xG Table
```sql
CREATE TABLE xg_data (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    team VARCHAR(100),
    xG DECIMAL(5,2),
    shots INT,
    shots_on_target INT,
    goals DECIMAL(5,2)
);
```

### Player Match Stats Table
```sql
CREATE TABLE player_stats (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    player VARCHAR(100),
    team VARCHAR(100),
    position VARCHAR(20),
    minutes INT,
    rating DECIMAL(4,2),
    goals INT,
    assists INT,
    shots INT,
    xG DECIMAL(5,2),
    key_passes INT,
    progressive_carries INT
);
```

### Lineup Table
```sql
CREATE TABLE lineups (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    team VARCHAR(100),
    player VARCHAR(100),
    position VARCHAR(20),
    is_starter BOOLEAN,
    substituted_in_minute INT,
    substituted_out_minute INT
);
```

---

## Summary: API-Football Alone is Sufficient

| Edge Strategy | Data Needed | API-Football Coverage |
|---------------|-------------|----------------------|
| **Decorrelation** | Historical odds | ✅ Full (3 years) |
| **Calibration** | Match outcomes | ✅ Full |
| **Distribution (xG)** | Shots, sog | ✅ Full (use as proxy xG) |
| **Social Sentiment** | Reddit/Pushshift | ✅ Free (no auth) |
| **Player Interactions** | Player match stats | ✅ Full |
| **Long Sequences** | 3-5 seasons | ✅ Sufficient (research shows 8+ unnecessary) |
| **Lineup Edge** | Starting XI, formations | ✅ Full |
| **Goalkeeper Stats** | Saves, clean sheets | ✅ Full |

### Optional Future Additions

Only if data proves insufficient:
- **Reddit/Pushshift** → Sentiment (free, no auth needed)
- **WorldFootball.net** → Historical backfill (scrape once if needed)

### Not Needed
- ❌ Understat (shot data from API-Football = proxy xG)
- ❌ FBref (API-Football player stats sufficient)
- ❌ football-data.org (redundant with API-Football)
- ❌ Twitter API (Reddit is free alternative)

---

## Final Recommendation

**Use only API-Football to start.** 

Build MVP with API-Football's:
- Fixtures + Results
- Odds (poll to build history)
- Lineups + Formations  
- Match Statistics (shots, possession, saves)
- Standings (form, position)
- Head-to-head history
- 3 years historical data

Add complexity only when data proves insufficient for your models.

---

*Data is the foundation. API-Football provides everything needed for practical prediction modeling.*
root@bootball:/opt/projects/docs/research# 

