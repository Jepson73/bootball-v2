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
