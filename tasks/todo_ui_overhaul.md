# Tasks / Todo - Football Prediction System

---

## PHASE 14: Web UI Overhaul - COMPLETED

### Changes Made

#### 1. Market Tabs Expansion ✅
- [x] Added "Over 1.5" tab (safer option, ~70%+ hit rate)
- [x] Added "Combo" tab (BTTS Yes + Over 2.5 combined)

#### 2. Top Value Bets Enhancement ✅
- [x] Show "Edge %" when model prob > implied by 10%+
- [x] Color code: Green (+10%+), Yellow (5-10%), Red (<5%)
- [x] Highlight "Sweet Spot" odds (1.85-2.20 for BTTS) with "SWEET" badge

#### 3. League Indicators ✅
- [x] Star/highlight for high-scoring leagues (Bundesliga, Eredivisie, MLS, Swiss, A-League, J1)
- [x] Show league BTTS % historical rate on matches
- [x] League filter toggle: "All Leagues" / "High-Scoring Only"

#### 4. Combined Markets Display ✅
- [x] Show BTTS + Over 2.5 combo odds (calculated as product of individual odds)
- [x] Display both component probabilities in combo view

#### 5. Better Styling ✅
- [x] Improved match card layout with headers
- [x] Better visual hierarchy with clear sections
- [x] Max-width 1000px for better readability

---

## Implementation Details

### Files Modified
- `src/models/overunder.py` - Added `predict_ou15()` function
- `scripts/web_ui.py` - Full overhaul:
  - Added `compute_edge()` function
  - Added `get_ou15_odds()` function
  - Added `HIGH_SCORING_LEAGUES` dict
  - Updated index route with new predictions
  - Complete rewrite of HTML template

### Sweet Spot Ranges (from lessons2.md)
- BTTS odds: 1.85-2.20 (backtesting: +37.84 units)
- High-scoring leagues: Bundesliga 62%, Eredivisie 62%, MLS 63%, Swiss 67%

### Edge % Calculation
- Edge = (model_prob - implied_prob) × 100
- Implied = 1/odds

---

## Status: COMPLETED ✅
