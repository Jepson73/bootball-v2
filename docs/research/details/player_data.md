# Player Data & Match Impact Analysis

## API-Football Player Endpoints

### Available Endpoints

| Endpoint | What It Returns | Use Case |
|----------|-----------------|--------|
| `/injuries` | Injured players list | Key absences affect goals |
| `/players` | Player stats (season) | Form, cards, goals |
| `/lineups` | Starting XI | Team strength |
| `/players/fixtures/{id}` | In-match stats | Live performance |
| `/top_scoreboard` | Goals, assists, cards | Top performers |

### Player Stats Fields (from /players)

```json
{
  "player_name": "Mohamed Salah",
  "player_id": "302",
  "team_name": "Liverpool",
  "position": "F",
  "player_goals": 18,
  "player_assists": 12,
  "player_yellow_cards": 3,
  "player_red_cards": 0,
  "player_injured": "No",
  "player_minutes": 2340,
}
```

---

## How Player Data Affects Match Outcomes

### 1. INJURIES → Goal Impact

| Situation | Effect | Betting Action |
|-----------|--------|---------------|
| Key striker out | -0.3 to -0.5 goals expected | Under goals |
| Best defender out | +0.2 to +0.3 goals for opponent | Over goals |
| Midfielder out | Low-moderate impact | Small adjustment |
| Multiple injuries | Cumulative | Reassess entirely |

**Key Players to Track:**
- Starting XI quality
- Top scorers
- Creative midfielders  
- First-choice goalkeeper
- Central defenders

### 2. LINEUPS → Team Strength

| Factor | Impact |
|--------|--------|
| Strong XI vs rotated | +0.3 goals |
| Formation change | Varies |
| Bench quality | Late-game advantage |

### 3. PLAYER CARDS → Match Intensity

| Player | Yellow/Red Risk |
|--------|-----------------|
| Defensive midfielders | High fouls |
| Aggressive defenders | Yellow accumulation |
| Players with prior cards | Likely more |
| Derbies | More cards |

---

## Player-Based Features for Models

### Feature Ideas

```python
# From player data, create:

# Goal features
team_avg_goals = avg(top_3_scorers.team_goals)
team_top_scorer_out = 1 if key_player_injured else 0
key_midfielder_available = 1 if creative_mid_available else 0

# Cards features  
team_total_yellows = sum(players.player_yellow_cards)
team_aggressive_players = count(players.yellow_cards > 3)
derby_match = 1 if rivalry else 0

# Fitness features  
team_minutes_played = avg(players.player_minutes)
injury_count = count(players.player_injured == "Yes")
backup_playing = 1 if squad_depth_low else 0
```

### Injury Impact Estimates

| Position | Weight | Reasoning |
|----------|--------|-----------|
| Striker | -0.40 | Goals directly lost |
| Winger | -0.25 | Creativity loss |
| Central Defender | -0.20 | Set piece advantage |
| Goalkeeper | -0.15 | Confidence/everything |
| Midfielder | -0.10 | Mixed |

---

## Data Fetching Plan

### Priority Data to Fetch (API-Football)

```python
# FIXTURE DATA (existing)
fixture_id, league_id, home_team, away_team, date

# INJURIES (NEW - Priority 1)
GET /injuries?league={id}&team={team_id}
Returns: player_id, player_name, type, status

# LINEUPS (Priority 1)
GET /lineups?match_id={id}
Returns: lineup XI, substitutes, formation  

# PLAYER STATS (Priority 2)  
GET /players?team={id}&season={year}
Returns: goals, assists, cards, minutes

# CARDS (Priority 2)
GET /top_scoreboard?league={id}&type=yellow_cards
Returns: top card earners per team
```

### Frequency

| Data Type | When to Fetch | Cost |
|-----------|--------------|------|
| Injuries | Before match | Low |
| Lineups | 1-2 hours before | Medium |
| Player stats | Daily update | Low |
| Cards leaders | Weekly | Low |

---

## API Call Examples

### Get Injuries for a Fixture
```
GET https://api-football.com/v3/injuries?fixture=12345
```

### Get Lineups  
```
GET https://api-football.com/v3/fixtures/lineaways?fixture=12345
```

### Get Teams Cards (for cards betting)
```
GET https://api-football.com/v3/topscorers?league=39&season=2024&type=yellow_cards
```

---

## Integration with Existing Pipeline

### Step 1: Fetch injuries before match
```python
def get_injury_impact(team_id, opponent_id):
    injuries = api.get_injuries(team=team_id)
    opponent_injuries = api.get_injuries(team=opponent_id)
    
    # Calculate weighted impact
    goal_impact = sum(injuries.striker_out * -0.4,
                    injuries.defender_out * -0.2)
    
    return goal_impact
```

### Step 2: Adjust predictions
```python
# In existing prediction
base_pred = model.predict(features)
injury_adj = get_injury_impact(home_id, away_id)

final_pred = base_pred + injury_adj
```

### Step 3: For cards betting
```python
def get_cards_features(home_id):
    players = api.get_players(team=home_id)
    total_yellows = sum(p.yellow_cards for p in players)
    aggressive = count(p.yellow_cards > 3)
    
    return {"total": total_yellows, "aggressive_players": aggressive}
```

---

## What API-Football Currently Has

| Data | Available | Price Tier |
|------|------------|-----------|
| Fixtures | ✅ | Basic + |
| Odds | ✅ | Basic + |
| Standings | ✅ | Basic |
| Top Scorers | ✅ | Basic |
| Injuries | ✅ | Premium |
| Lineups | ✅ | Premium |
| Player Stats | ✅ | Premium |
| Live Stats | ✅ | Premium |

---

## References

- https://www.api-football.com/documentation-v3 - Full docs
- https://www.api-football.com/news/post/new-endpoint-injuries - Injuries endpoint
- https://apifootball.com/documentation/ - Legacy docs

---

*Last Updated: 2026-04-12*
*Category: Player Data Integration*