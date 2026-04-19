# Betting Architecture

Modular structure for adding new betting markets.

---

## Directory Structure

```
src/betting/
  markets.py      # Market definitions (enum-like)
  ev.py           # Expected Value calculation
  kelly.py        # Kelly criterion sizing
  shin.py         # Shin method (odds margin removal)
  value_bets.py  # Value bet detection

src/models/
  btts.py        # Both Teams To Score model
  overunder.py   # Over/Under 2.5 goals model
  # ... existing models for 1X2

scripts/
  web_ui.py      # Web dashboard
```

---

## Adding a New Market

### Step 1: Define Market (src/betting/markets.py)

```python
from src.betting.markets import BetMarket, MarketDef

BetMarket.NEW = "new"

MARKETS[BetMarket.NEW] = MarketDef(
    name="Market Name",
    outcomes={"K1": "Option 1", "K2": "Option 2"},
    bet_type_id=99,  # API-Football bet type ID
    description="What this market predicts",
)
```

### Step 2: Create Prediction Model (src/models/new_market.py)

```python
from dataclasses import dataclass

@dataclass
class NewMarketResult:
    prob_option1: float
    prob_option2: float

class NewMarketPredictor:
    def predict_proba(self, home_id: int, away_id: int) -> NewMarketResult:
        # Your prediction logic
        return NewMarketResult(prob_option1=0.6, prob_option2=0.4)

def predict_new_market(home_id: int, away_id: int) -> tuple[float, float]:
    """Simple function interface - returns (prob_opt1, prob_opt2)"""
    predictor = NewMarketPredictor()
    result = predictor.predict_proba(home_id, away_id)
    return result.prob_option1, result.prob_option2
```

### Step 3: Add Odds Fetching (scripts/web_ui.py)

In the main loop where fixtures are fetched:

```python
# Get odds for NEW market
new_odds = {}
new_ev = {}
try:
    raw = client.get_odds(fixture_id, bet_type=99)  # bet_type from markets.py
    if raw:
        for bm in raw:
            bets = bm.get("bookmakers", [{}])[0].get("bets", [])
            for bet in bets:
                if bet.get("name") == "Market Name":
                    for v in bet.get("values", []):
                        val = v.get("value")
                        odd = float(v.get("odd", 0))
                        if odd > 0:
                            new_odds[val] = odd
                    break
except:
    pass

# Get model predictions
prob_opt1, prob_opt2 = predict_new_market(home_id, away_id)

# Calculate EV
if new_odds:
    for short, long in {"K1": "Option 1", "K2": "Option 2"}.items():
        prob = prob_opt1 if short == "K1" else prob_opt2
        odd = new_odds.get(long, 0)
        if odd > 0:
            new_ev[short] = round((prob * odd - 1) * 100, 1)
```

### Step 4: Add to Template Data

```python
leagues.append({
    # ... existing fields
    "new_prob_opt1": round(prob_opt1, 2),
    "new_prob_opt2": round(prob_opt2, 2),
    "new_odds": new_odds,
    "new_ev": new_ev,
})
```

### Step 5: Add Market Tab (scripts/web_ui.py)

Add to HTML:

```html
<div class="market-tabs">
    <div class="market-tab" onclick="setMarket('h2h')">1X2</div>
    <div class="market-tab" onclick="setMarket('btts')">BTTS</div>
    <div class="market-tab" onclick="setMarket('ou')">O/U 2.5</div>
    <div class="market-tab" onclick="setMarket('new')">New Market</div>
</div>
```

### Step 6: Add Rendering (JavaScript)

In renderMatches():

```javascript
} else if (activeMarket === 'new') {
    container.innerHTML = filtered.map(f => `
        <div class="outcome">
            <div class="out-label">Option 1</div>
            <div class="out-prob">${(f.new_prob_opt1 * 100).toFixed(0)}%</div>
            <div class="out-odd">${f.new_odds?.['Option 1'] || '-'}</div>
            <div class="out-ev">${f.new_ev?.K1 > 0 ? '+' + f.new_ev.K1 : f.new_ev?.K1 || '-'}</div>
        </div>
        <div class="outcome">
            <div class="out-label">Option 2</div>
            <div class="out-prob">${(f.new_prob_opt2 * 100).toFixed(0)}%</div>
            <div class="out-odd">${f.new_odds?.['Option 2'] || '-'}</div>
            <div class="out-ev">${f.new_ev?.K2 > 0 ? '+' + f.new_ev.K2 : f.new_ev?.K2 || '-'}</div>
        </div>
    `).join('');
}
```

---

## Market Bet Type IDs

From API-Football:
| ID | Market |
|----|-------|
| 1 | Match Winner (1X2) |
| 5 | Both Teams To Score |
| 4 | Over/Under 2.5 |
| 3 | Asian Handicap |
| 6 | Correct Score |
| 7 | Half Time/Full Time |

---

## Key Files

- `src/betting/markets.py` - Market definitions
- `src/models/btts.py` - Example BTTS model
- `src/models/overunder.py` - Example O/U model
- `scripts/web_ui.py` - Web UI (lines ~470-490 for odds fetching)