# Model Calibration & Confidence

## Key Insight

**A well-calibrated 55% prediction that wins 55% of the time beats a "more accurate" 70% prediction that only wins 50%.**

Research source: "Calibration vs Accuracy" - +34.69% ROI for calibration-optimized models vs -35.17% for accuracy-optimized.

---

## What is Calibration?

Calibration answers: **"When our model says 60%, does it actually happen 60% of the time?"**

### Calibration Example
| Our Probability | Times Predicted | Times Occurred | Calibration |
|----------------|-----------------|---------------|-------------|
| 55-60% | 100 times | 58 times | ✅ Well-calibrated |
| 55-60% | 100 times | 45 times | ❌ Miscalibrated (overconfident) |
| 55-60% | 100 times | 65 times | ❌ Miscalibrated (underconfident) |

---

## Why Calibration Matters More Than Accuracy

### The Problem with Accuracy
- Predicting winners (70% accuracy) doesn't mean your probabilities are correct
- You could predict Home wins every time and get 70% accuracy but all probabilities wrong
- Bookmakers exploit miscalibration

### The Calibration Advantage
- Correct probabilities allow proper EV calculation
- Identifies TRUE value bets (where you have edge)
- Allows proper bankroll management (kelly criterion needs correct probabilities)

### Research Findings
```
Calibration-optimized models: +34.69% ROI
Accuracy-optimized models:   -35.17% ROI
```

**Source**: arxiv.org/pdf/2303.06021v3.pdf

---

## Metrics for Calibration

### 1. Brier Score
Measures probability accuracy (0 = perfect, 0.25 = random for 3 outcomes)

```python
Brier Score = Σ(probability - outcome)² / N
```

**Our target**: < 0.25 for 3-outcome markets, < 0.20 for 2-outcome markets

### 2. Expected Calibration Error (ECE)
Measures how far off the calibration curve is from perfect:

```python
ECE = Σ(bin_weight × |accuracy - confidence|)
```

**Target**: < 0.05 (5% miscalibration acceptable)

### 3. Reliability Diagrams
Plot predicted probability vs actual hit rate per probability bin.

---

## Confidence vs Probability

These are **different things**:

### Probability (P)
- Point estimate of expected outcome
- "This bet has 65% chance of winning"
- Used for EV calculation

### Confidence
- How much we trust that probability estimate
- Based on:
  - **Sample size**: 10k matches training vs 100 matches
  - **Model stability**: Similar inputs → similar outputs
  - **Data quality**: Complete stats vs missing data
  - **League maturity**: Premier League vs lower divisions

### True Confidence Format
```json
{
  "probability": 0.65,
  "confidence": {
    "interval": [0.55, 0.75],
    "sample_size": 15420,
    "model_certainty": 0.82
  }
}
```

---

## How to Implement Calibration

### 1. Isotonic Regression (Best)
```python
from sklearn.isotonic import IsotonicRegression

# Train model, get raw probabilities
raw_probs = model.predict_proba(X_test)

# Calibrate using isotonic regression
calibrator = IsotonicRegression(out_of_bounds='clip')
calibrator.fit(raw_probs, y_test)

# Use calibrated probabilities
calibrated_probs = calibrator.predict(raw_probs)
```

### 2. Platt Scaling (Logistic Regression)
```python
from sklearn.linear_model import LogisticRegression

calibrator = LogisticRegression()
calibrator.fit(raw_probs.reshape(-1, 1), y_test)
calibrated_probs = calibrator.predict_proba(raw_probs.reshape(-1, 1))[:, 1]
```

### 3. Temperature Scaling (Neural Networks)
```python
# Divide logits by temperature T before softmax
T = optimal_temperature  # Found via validation
scaled_probs = softmax(logits / T)
```

---

## Tracking Calibration in Production

### Per-Market Brier Score
| Market | Brier Score | Status |
|--------|-------------|--------|
| H2H | 0.198 | ✅ Good (< 0.25) |
| BTTS | 0.312 | ⚠️ Poor (> 0.25) |
| O/U 2.5 | 0.187 | ✅ Good |
| O/U 1.5 | 0.223 | ✅ Good |

### Calibration Update Frequency
- **Weekly**: Recalculate Brier score on settled predictions
- **Monthly**: Retrain calibrator on recent data
- **Quarterly**: Full model + calibrator retrain

---

## Confidence Intervals by Market

### How to Calculate
1. **Bootstrap resampling**: Sample with replacement, recalculate probability
2. ** Bayesian inference**: Beta distribution for 2-outcome, Dirichlet for 3-outcome
3. **Ensemble variance**: Std deviation across multiple models

### Example Thresholds
| Confidence Source | Low (< 30%) | Medium (30-60%) | High (> 60%) |
|-----------------|-------------|-----------------|--------------|
| Sample size | < 1000 matches | 1000-10000 | > 10000 |
| Model agreement | < 50% models agree | 50-80% | > 80% |
| Data completeness | > 20% missing | 5-20% | < 5% |

---

## UI Display Recommendations

### Current (Wrong)
```
Confidence: 71%
```
This shows probability, not confidence.

### Better (Research-Based)
```
Probability: 71%
Confidence: HIGH (based on 15,420 matches)
Confidence Interval: 68-74%
```

### Even Better (Calibrated + Confidence)
```
Calibrated Probability: 68%  ← After isotonic regression
True Confidence: 72%         ← How often 65-70% bets hit
Confidence Interval: 62-78%   ← Based on model uncertainty
Evidence Strength: STRONG    ← Sample size + data quality
```

---

## Implementation Roadmap

### Phase 1: Measure (Week 1-2)
- [ ] Add Brier score calculation per market
- [ ] Track calibration metrics in DB
- [ ] Create calibration dashboard

### Phase 2: Calibrate (Week 3-4)
- [ ] Implement isotonic regression calibrator
- [ ] Apply calibrator to all market predictions
- [ ] Verify Brier score improves

### Phase 3: Confidence (Week 5-6)
- [ ] Add sample size tracking per prediction
- [ ] Calculate confidence intervals
- [ ] Display confidence in UI

### Phase 4: Adaptive (Week 7+)
- [ ] Auto-retrain calibrator when Brier degrades
- [ ] Per-league calibration (some leagues miscalibrate more)
- [ ] Confidence-based bet sizing

---

## References

### Academic
- [Calibration vs Accuracy Paper](https://arxiv.org/pdf/2303.06021v3.pdf) - +34.69% ROI validation
- [Probabilistic Sports Forecasting](https://www.science.org/doi/10.1126/science.1239891) - Calibration methodology
- [Beyond Accuracy](https://arxiv.org/abs/1706.04599) - ECE and calibration curves

### Implementation
- [Sklearn Calibration Docs](https://scikit-learn.org/stable/modules/calibration.html)
- [Reliability Diagrams](https://www.statology.org/reliability-diagram/)

---

## Summary: The Calibration Hierarchy

1. **Raw Model Output** → Base probability (often miscalibrated)
2. **Isotonic Calibration** → Corrected probability (well-calibrated)
3. **Confidence Interval** → Uncertainty around calibrated prob
4. **Kelly Criterion** → Bet size based on calibrated prob + edge
5. **ROI** → Final result of calibrated betting

**Key insight**: Without calibration, your EV calculations are wrong, and Kelly sizing fails.

---

## Algorithm Comparison for Sports Prediction

### Available Algorithms

| Algorithm | Pros | Cons | Calibration Behavior |
|-----------|------|------|---------------------|
| **sklearn GradientBoosting** | Simple, no extra deps | Slow, less tuning options | Tends to push probabilities toward extremes |
| **XGBoost** | Fast, regularization, tree pruning | Needs installation | Similar to GBM but more controlled |
| **LightGBM** | Fastest on large data, histogram-based | Can overfit on small data | Good probability estimates |
| **CatBoost** | Handles categoricals well, robust | Slower than LightGBM | Well-calibrated out of box |
| **RandomForest** | Robust, parallel | Sigmoid-shaped miscalibration | Poor near 0/1 |
| **LogisticRegression** | Naturally calibrated | Linear only | Perfectly calibrated (balance property) |

### Research Findings

1. **Gradient Boosting Methods (GBM, XGBoost, LightGBM)**
   - Generally best for structured/tabular sports data
   - sklearn's GBM tends to push probabilities to extremes
   - XGBoost/LightGBM offer better regularization and control

2. **Random Forest**
   - Tends to make predictions near 0.2 and 0.9, rarely 0 or 1
   - Results in characteristic sigmoid calibration curve
   - Variance in base trees causes bias away from extremes

3. **Calibration is Algorithm-Agnostic**
   - The key finding: **calibrated model > most accurate model**
   - +34.69% ROI with calibration vs -35.17% with accuracy-only optimization
   - Source: Walsh & Joshi (2023) on NBA betting

### Recommendation for Bootball

1. **Primary**: Use LightGBM (fastest, often best) with isotonic calibration
2. **Alternative**: Try XGBoost for comparison
3. **Fallback**: sklearn GradientBoosting (always available)

**Important**: If models show no edge, it's likely:
- Feature quality/data issues (not model choice)
- Market efficiency (Odds don't offer value)
- Calibration needed before betting decisions

### Installation

```bash
# LightGBM (recommended)
pip install lightgbm

# XGBoost (alternative)
pip install xgboost

# Both available
pip install lightgbm xgboost
```

### Quick Comparison Test

```python
from sklearn.ensemble import GradientBoostingClassifier
import lightgbm as lgb
import xgboost as xgb

# All three can be compared with same calibration approach
models = {
    'GBM': GradientBoostingClassifier(n_estimators=200, max_depth=4),
    'LightGBM': lgb.LGBMClassifier(n_estimators=200, max_depth=4, verbose=-1),
    'XGBoost': xgb.XGBClassifier(n_estimators=200, max_depth=4, use_label_encoder=False)
}

for name, model in models.items():
    model.fit(X_train, y_train)
    probs = model.predict_proba(X_test)[:, 1]
    calibrated = isotonic_calibrator.fit_transform(probs, y_test)
    print(f"{name}: Brier={brier_score(y_test, calibrated):.4f}")
```
