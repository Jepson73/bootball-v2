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
