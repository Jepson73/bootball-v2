# Prediction Models: Beating the Bookmakers

## The Key Insight

**Calibration > Accuracy** for making money!

Research shows: optimizing for calibration yields +34.69% ROI vs -35.17% for accuracy-optimized models.

---

## What Actually Works

### 1. Ensemble Models (Best)
- **Dixon-Coles (xG) + Elo blend**: Outperforms either alone
- Combine two different mathematical perspectives
- Average the probabilities, renormalize

### 2. XGBoost (Gold Standard in 2026)
- 67% match outcome accuracy
- Best features: form, xG, head-to-head, home/away performance
- Requires calibration (isotonic or platt)

### 3. Key Features (Most Important)
1. Rolling form (recent results, weighted)
2. Expected Goals (xG) metrics
3. Head-to-head records
4. Home/away differentials
5. Player availability
6. Elo/rating values

---

## Critical Findings

### ❌ What Doesn't Work
- Betting short-odds favorites (1.0-2.0x) → LOSES money even with high win rate
- Maximizing accuracy instead of calibration
- Following bookmaker predictions
- Single model approaches

### ✅ What Does Work
- **Find LONG-ODDS value** - profit comes from 3.5x+ odds, not 1.3x odds
- **Reduce correlation with bookmaker odds** - if you think same as bookmaker, you can't profit
- **Calibrated probabilities** - when model says 60%, happens ~60%
- **Ensemble models** - combine Dixon-Coles + Elo

---

## The Math That Matters

### EV Formula (For Understanding)
```
EV = Your_Probability × (Odds - 1) - (1 - Your_Probability)
```

### But Better: Beat the Bookmaker
```
Only bet when: Your_Probability > Bookmaker_Implied_Probability
```

Where: Bookmaker_Implied = 1/Odds (remove vig first with Shin method)

### CLV (Closing Line Value)
```
CLV = Your_Odds_Before - Closing_Odds
```
- Positive CLV = sharp bettor
- +5% consistent CLV = elite

---

## Winning Strategy

1. **Build calibrated model** (isotonic regression)
2. **Find edge** where your prob > bookmaker implied
3. **Only bet long odds** (3.5x+) where you have edge
4. **Track CLV** - did you beat the close?
5. **Volume** - need 500+ bets to validate

---

## Model Comparison

| Model | Accuracy | RPS | Best For |
|-------|----------|-----|---------|
| XGBoost | 67% | 0.216 | Match outcomes |
| LightGBM | 54% | 0.190 | Calibrated probs |
| Elo | 52-55% | 0.202 | Team rankings |
| Poisson | 60-65% | 0.200 | Goals |
| Ensemble | 70%+ | 0.193 | All markets |

---

## Sources

### Best Research
- https://medium.com/@omnimahui/profitable-england-premier-league-betting-strategy - +107% ROI with LightGBM
- https://wagerbase.io/blog/how-wagerbase-ensemble-prediction-engine-works - xG-Elo ensemble
- https://export.arxiv.org/pdf/2303.06021v3.pdf - Calibration > Accuracy (+34.69% vs -35.17%)
- https://www.sciencedirect.com/science/article/abs/pii/S016920701930007X - Reduce bookmaker correlation

### Implementation
- XGBoost with isotonic calibration
- Dixon-Coles + Elo ensemble
- Track CLV, not just wins

---

*Add to research notes*