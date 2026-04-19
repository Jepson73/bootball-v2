# Advanced Markets: Corners, Cards & Alternative Markets

## Corners Betting

### Average Corners by League (Major Leagues)

| League | Avg Corners/Match | Over 9.5 Hit % | Notes |
|--------|------------------|---------------|-------|
| Premier League | 10.6-10.84 | 59% | Fast tempo, wide play |
| Championship | 10.51 | 55% | High volume |
| League Two | 10.95 | 61% | Highest UK |
| Bundesliga | 10.1 | ~55% | High shot volume |
| Serie A | 9.4 | ~50% | Tactical, lower |
| Ligue 1 | ~9.5 | ~52% | Moderate |

### Best Leagues for Corners (Under-Radar)

| League | Why Good | Strategy |
|--------|----------|----------|
| **Eerste Divisie** (Netherlands) | Attack-at-all-costs | High total corners |
| **A-League** (Australia) | Wide play, physical | Home team corners |
| **Swiss Super League** | Perpetual attack | High overs |
| **J-League** | Technical, tenacity | In-play value |
| **Norway Eliteserien** | Home dominance | Home corners |

### Key Stats for Corner Model (7 That Matter)

1. **Team corners for** (avg per match)
2. **Team corners against** (conceded)
3. **Home/away splits** (huge difference!)
4. **Shots on target** → correlates with corner opportunities
5. **Crosses into box** → direct corner source
6. **Defensive style** (deep defending = more corners against)
7. **Referee tendencies** (stoppages = corners)

### Why Corners Are Profitable
- Less efficient than goal markets
- Less money bet = less sharp lines
- Higher frequency = lower variance than goals
- Bookmakers model less rigorously than goals

### Corner Bet Types

| Market | Description | Typical Line |
|--------|-------------|---------------|
| Total Corners Over/Under | Total corners in match | 9.5, 10.5 |
| Team Corners | One team's corners | 5.5, 6.5 |
| Asian Corners | Handicap with halves | 10, 10.5 |
| Race to 3/5/7 Corners | First to X corners | - |
| Corners per Half | 1H / 2H split | - |

---

## Cards Betting

### Average Cards by League

| League | Yellows/Match | Reds | Notes |
|--------|---------------|-----|-------|
| Premier League | ~2.5 | ~0.1 | Less physical |
| Serie A | ~3.5 | ~0.15 | More physical |
| La Liga | ~3.2 | ~0.12 | Medium |
| Ligue 1 | ~3.0 | ~0.1 | Moderate |
| South American leagues | ~4.0+ | ~0.2+ | High |

### Card Drivers

1. **Game state**: Losing team fouls more (60-80 min)
2. **Derby matches**: More tension = more cards
3. **Referee tendencies**: Some refs stricter
4. **Team discipline history**: Track record matters
5. **late goals**: Frustration reds
6. **Tactical fouls**: Stop counters

### When Cards Increase (value)
- After 60' when team is trailing
- 70-80 minute mark (late frustration)
- Derby/ rivalry matches
- Weak referee = expect more

### Card Bet Types

| Market | Description |
|--------|-------------|
| Over/Under Total Cards | Total yellows + reds |
| Team Cards | One team's cards |
| First/Last Card | Timing bets |
| Referee Markets |特定 referee |

---

## Other Alternative Markets

### Goalscorer Markets
- **First Goalscorer**: High odds, hard to predict
- **Anytime**: Most popular
- **Hat-trick**: Very high variance

### Halves Markets
- **First Half Goals**: Over 0.5, 1.5
- **Second Half Goals**: Often more than 1H
- **BTTS Halves**: Score in each half

### Asian Handicaps
- Complex but valuable
- Better for balanced matches
- Removes draw

### Draws
- 25-27% of matches
- Highest margin for bookies
- Low confidence = high value potential

---

## League Targeting Summary

### Best for GOALS (BTTS/Over)
- Bundesliga, Eredivisie, MLS, A-League, J-League

### Best for CORNERS
- Premier League, Championship, Eerste Divisie
- A-League, Swiss Super League

### Best for CARDS
- Serie A, La Liga, South American leagues
- Derby matches in any league

### Best OVERALL value (less efficient)
- Smaller leagues (Eerste Divisie)
- Lower-tier divisions
- Non-Premier League markets

---

## API-Football Odds Coverage

### Available Bet Types (via API)

```python
# Common bet_type_ids
BET_TYPES = {
    1: "Match Winner",      # 1X2
    3: "Asian Handicap",
    4: "Over/Under 2.5",
    5: "BTTS",
    6: "Correct Score",
    7: "Half Time/Full Time",
    8: "Corners",           # May not be available
    9: "Cards",            # May not be available
    12: "Goal Intervals",
    14: "Both Teams To Score 1st Half",
    15: "Both Teams To Score 2nd Half",
}
```

### Data to Fetch (Priority)
1. ✅ Match Winner (1X2)
2. ✅ Over/Under 2.5 
3. ✅ BTTS
4. ⚠️ Over/Under 1.5 (if available)
5. ⚠️ Corners (limited)
6. ⚠️ Cards (limited)

---

## Sources

- https://performanceodds.com/football-stats-trends - Statistics hub
- https://thepuntlab.com/set-piece-efficiency-corner-kick-edge - Corner edges
- https://performanceodds.com/betting-tricks/corners-predictions-explained - 7 stats
- https://thewagertheorem.com/football-leagues-corner-betting - Best leagues
- https://statshub.com/betting-academy/betting-on-corners - UK league data
- https://blog.20bet.com/betting-guide/small-markets-betting-guide-corners-cards-props - Alternative markets

---

*Last Updated: 2026-04-12*
*Category: Alternative Markets*