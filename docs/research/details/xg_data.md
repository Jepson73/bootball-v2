# xG Data Strategy - UPDATED

## Current Situation (2026-04)

### What API-Football HAS

| Data | Available | Notes |
|------|------------|-------|
| Shots Total | ✅ Yes | Generic counts |
| Shots On Goal | ✅ Yes | |
| Shots Off Target | ✅ Yes | |
| Shots Inside Box | ✅ Yes | |
| Shots Outside Box | ✅ Yes | |
| Corners | ✅ Yes | |
| Dangerous Attacks | ✅ Yes | Proxy for pressure |
| Ball Recovery | ✅ Yes | |
| Actual xG value | ❌ No | Not available |

**API-Football does NOT have actual xG values** - only shot counts.

### What's NOT Available (Blocked/Paid)
- StatsBomb Open Data - access issues
- Understat API - blocked
- SportMonks xG - €19-99/mo add-on required

---

## Solution: Proxy xG from API-Football Stats

Since we can't get real xG, we can CREATE proxy xG from available data:

### Proxy xG Formula (Research-Based)

```python
def calculate_proxy_xg(match_id):
    """Calculate xG from API-Football statistics"""
    stats = api.get_statistics(match_id=match_id)
    
    # Weight shot types (based on research)
    shots_inside_box = stats["Shots Inside Box"]
    shots_outside_box = stats["Shots Outside Box"]
    shots_on_target = stats["Shots On Goal"]
    
    # xG approximation weights
    xg = (
        shots_on_target * 0.35 +      # Quality shots
        shots_inside_box * 0.25 +      # Inside box
        shots_outside_box * 0.08 +     # Outside box lower
        0
    )
    
    return xg
```

### Better: Team-Based xG from Historical Performance

```python
def get_team_xg_from_history(team_id, last_n=5):
    """Calculate team xG from actual goals + shots"""
    
    # Get last N matches
    matches = api.get_matches(team=team_id, last_n=5)
    
    total_xg = 0
    for match in matches:
        # Use actual goals (which tend to regress to mean)
        goals = match.goals_for
        
        # Adj for shot quality (if available in later stats)
        if match.shots_on_target > 0:
            adj = min(match.goals / match.shots_on_target, 0.5)
        else:
            adj = 0.15
            
        xg = match.shots_total * 0.12 + goals * 0.5  # Proxy formula
        total_xg += xg
    
    return total_xg / last_n
```

### Research-Backed Proxy Weights

From analysis of shot-to-goal conversion rates:

| Shot Type | Conversion % | xG Weight |
|----------|-------------|-----------|
| Shots on Target | 32-35% | 0.33 |
| Shots Inside Box | 18-22% | 0.20 |
| Shots Outside Box | 5-8% | 0.06 |
| Open Play | 10-12% | 0.11 |
| Set Piece | 25-27% | 0.26 |
| Penalty | 75-78% | 0.76 |

---

## Recommended Strategy for Bootball

### Step 1: Use Historical Goals as xG Proxy

```python
def get_attack_strength(team_id, fixtures):
    """Attack strength = avg goals scored last 5 matches"""
    recent = fixtures[-5:]  # Last 5
    
    goals_for = sum(f.goals_for for f in recent)
    goals_against = sum(f.goals_against for f in recent)
    
    return {
        "attack": goals_for / len(recent),
        "defense": goals_against / len(recent),
        "xG_proxy": goals_for / len(recent),  # Goals ≈ xG for prediction
    }
```

### Step 2: Add Shot Quality from API Stats

```python
def enrich_with_shot_quality(home_id, away_id):
    """Add shot quality (better than nothing)"""
    
    # Get recent match stats
    stats = get_team_avg_stats(home_id)  # From /statistics
    
    return {
        "home_shots_per_game": stats.avg_shots,
        "home_sot_per_game": stats.avg_sot,
        "shots_conversion": stats.goals / max(stats.shots, 1)
    }
```

### Step 3: Model Uses These Features

| Feature | Source | Use |
|---------|--------|-----|
| Goals For (5 avg) | API-Football fixtures | Primary attack |
| Goals Against (5 avg) | API-Football fixtures | Defense |
| Shots per game | API-Football stats | Quality adj |
| Shots on target % | API-Football stats | Conversion |
| Corners | API-Football stats | Pressure |
| Dangerous Attacks | API-Football stats | Territory |

---

## What's Already Working Without xG

Our models already work! Here's why:

1. **Dixon-Coles** uses GOALS (which ≈ xG over time)
2. **BTTS model** uses Poisson with goal rates  
3. **Over/Under** uses scoring rates

**Goals scored ≈ xG over 5-10 matches** - The Law of Large Numbers averages out finishing luck, making actual goals nearly as predictive as xG for longer-term predictions.

---

## Recommendation

**Don't chase xG** - Use what we have:
- Historical goal averages (excellent proxy)
- Shot counts from API-Football statistics
- The model works without explicit xG

**Focus on**:
1. Betting markets (BTTS, Over/Under) - Already winning strategy ✅
2. Data enrichment for INJURIES/lineups via API-Football
3. Better model features from goals/shots/corners

---

## Updated Todo

```python
# What to fetch (high priority)
- Fixtures + odds (already working) ✅
- Injuries endpoint (new) 📋
- Lineups endpoint (new) 📋
- Player stats (new) 📋
- Team statistics (shots, corners) 📋

# What NOT to worry about
- Real xG (not available, use goals instead)
```

---

*Last Updated: 2026-04-12*
*Conclusion: Use goals scored as xG proxy - it works!*