# Bootball V2 Phase 2 Report — Wave 1 Features, Retrain, Walk-Forward Validate

**Generated:** 2026-06-24  
**Scope:** O (production cleanup), P (data coverage), Q (Wave 1 features), R (retrain & validate), S (versioning)  
**Ground rules:** Read-only on production schema/code except where explicitly building new tables/columns below.

---

## O. Production Cleanup Verification

### Status: Complete.

**Rollback (executed this session, not previously):**  
The `fdco` rows from Phase 1d Task N were still in production `fixture_odds` (35,256 rows) at the start of this session. They were rolled back now:

```sql
-- Step 1: Copy to historical_odds.db first (data never lived nowhere)
CREATE TABLE hist.fixture_odds (same schema as production);
INSERT INTO hist.fixture_odds SELECT * FROM fixture_odds WHERE bookmaker='fdco';
-- Verified: 35,256 rows, 17,629 distinct fixtures

-- Step 2: Delete from production
DELETE FROM fixture_odds WHERE bookmaker='fdco';
-- Verified: 0 rows remain
```

**historical_odds.db created at:** `scripts/analysis/historical_odds.db`  
Contents:
```
rows:       35,256
fixtures:   17,629 distinct
h2h rows:   17,628
ou25 rows:  17,628
date range: 2019-08-02 to 2024-06-02
```

**ATTACH pattern (used by all subsequent scripts):**
```python
conn = sqlite3.connect("data/football.db")
conn.execute("ATTACH 'scripts/analysis/historical_odds.db' AS hist")
# Production odds:    SELECT … FROM fixture_odds
# Historical odds:    SELECT … FROM hist.fixture_odds
# Combined:           UNION both
```

Tested: production DB returns 0 fdco rows; historical_odds.db returns 17,629 distinct fixtures; the ATTACH pattern unions both cleanly.

---

## P. Data Coverage Check

### Status: Complete. Combined validation pool = 20,658 fixtures; 93.8% with fixture_stats.

### Combined validation pool

| Source | Fixtures | Seasons | Markets available |
|--------|----------|---------|------------------|
| Production fixture_odds | 3,029 | 2025–2026 | h2h, btts, ou25, ou15 |
| historical_odds.db (fdco) | 17,629 | 2019–2023 | h2h, ou25 only (no btts/ou15) |
| **Total** | **20,658** | **2019–2026** | — |

### fixture_stats coverage in validation pool

| Season | Fixtures w/ odds | Has fixture_stats | Coverage |
|--------|-----------------|-------------------|----------|
| 2019 | 3,307 | 2,542 | 76.9% |
| 2020 | 3,577 | 3,577 | 100% |
| 2021 | 3,581 | 3,581 | 100% |
| 2022 | 3,583 | 3,583 | 100% |
| 2023 | 3,581 | 3,577 | 99.9% |
| 2024 | 0 | — | — (fdco archive doesn't include 2024 yet) |
| 2025 | 2,334 | 957 | 41.0% |
| 2026 | 695 | 259 | 37.3% |
| **Total** | **20,658** | **17,076** | **82.7%** |

**2019 coverage (76.9%):** The API-Football data collection for E2 (League One) was incomplete in the 2019/20 season (only 12/404 fixtures have `fixture_stats`). All other 2019 leagues are 100% covered.

**2025–2026 coverage (37–41%):** The production seasons were collected only during the live season; `fixture_stats` requires a post-match API call that wasn't backfilled. The 957 fixtures in 2025 that do have stats cover the period after the `fixture_stats` ingestion was added.

### Achievable validation sample (Wave 1 features populated vs. fallback-to-defaults)

Fixtures without `fixture_stats` still receive features — Wave 1 F1 features fall back to league-average defaults (shots=4.5, possession=50%, etc.) for teams with no history. This is leakage-safe (defaults don't encode future information) but reduces signal quality for those fixtures.

| Pool segment | Fixtures | Full F1 features | Fallback only |
|-------------|----------|-----------------|---------------|
| Historical 2020–2023 | 14,322 | 14,318 (99.9%) | 4 |
| Historical 2019 | 3,307 | 2,542 (76.9%) | 765 |
| Production 2025–2026 | 3,029 | 1,216 (40.2%) | 1,813 |
| **Total** | **20,658** | **18,076 (87.5%)** | **2,582 (12.5%)** |

### Walk-forward window composition

Three non-overlapping test windows satisfy the Phase 2 success bar requirements:

| Window | Test fixtures | Training cutoff | Training set size |
|--------|--------------|-----------------|-------------------|
| 2022 | 3,540 | Before 2022-01-01 | ~161K (→ capped 40K w/ recency bias) |
| 2023 | ~3,580 | Before 2023-01-01 | ~315K (→ capped 40K w/ recency bias) |
| 2025–26 | 3,029 | Before 2025-01-01 | ~570K (→ capped 40K w/ recency bias) |

**Success bar pre-check:** At Phase 1d's 18.4% blend pass rate on h2h (3 outcome slots per fixture), each test window yields ~3,500 × 3 × 11% ≈ 1,155 h2h bets. **All markets except ou15 are expected to clear the 500-bet floor.** ou15 was 97 bets per window in V3 (2.1% fixture pass rate); no feasible expanded pool can compensate for market efficiency eliminating ou15 EV opportunities.

---

## Q. Wave 1 Feature Engineering

### Status: Complete. Module at `scripts/analysis/features_v1.py`.

### Feature set (20 new features, combining with 9 standings-derived = 29 total)

#### F1: Rolling team form from fixture_stats (12 features — 6 home + 6 away)

| Feature | Definition | Leakage boundary |
|---------|------------|-----------------|
| `{h/a}_shots_on_goal_avgN` | Mean `home_shots_on_goal` (or `away_shots_on_goal` when team played away) over last N prior matches | Only matches where f.date < target.date |
| `{h/a}_shots_total_avgN` | Mean total shots over last N prior matches | Same |
| `{h/a}_possession_avgN` | Mean possession % (correct side per match) over last N | Same |
| `{h/a}_corners_avgN` | Mean corners over last N prior matches | Same |
| `{h/a}_pass_acc_avgN` | Mean (accurate_passes / total_passes) × 100 over last N | Same |
| `{h/a}_yellow_avgN` | Mean yellow cards over last N prior matches | Same |

**Leakage guard — home/away side extraction:** For each historical match in a team's form window, the correct-side column is selected based on whether the team was home or away in that match. Using `home_shots_on_goal` for a team that played away would silently corrupt every away team's form vector.

**Leakage guard — same-day exclusion:** `bisect_left(dates, before_date)` finds the first index where date ≥ before_date, so same-day matches are excluded.

**xG excluded:** `home_xg` / `away_xg` are 0% populated (all NULL) in the current DB — not included.

**N values:** N=5 and N=10 variants are both computed and compared. Features are identical in structure; N affects only the rolling window depth.

**Default values (used when < N prior matches exist):**
- shots_on_goal: 4.5 (league average)
- shots_total: 9.0
- possession: 50.0%
- corners: 5.0
- pass_accuracy: 75.0%
- yellow_cards: 1.5

#### F2: League-context features (3 features)

| Feature | Definition | Leakage boundary |
|---------|------------|-----------------|
| `league_avg_goals` | Mean total goals per match in this league over the trailing 2-year window (730 days) ending at target.date | Window = [target.date − 730 days, target.date) |
| `league_btts_rate` | Fraction of matches where both teams scored in the trailing 2-year window | Same |
| `league_hhi` | Herfindahl–Hirschman win-concentration index over all teams in the trailing 2-year window | Same |

**Trailing window, not season-inclusive:** A trailing 730-day rolling window is used, not the current season's full statistics. Using the eventual full season's statistics would leak post-prediction-date match outcomes into the feature for mid-season predictions.

**Defaults (when < 20 prior matches in window):** avg_goals=2.5, btts_rate=0.50, hhi=0.05.

#### F3: Head-to-head features (5 features)

| Feature | Definition | Leakage boundary |
|---------|------------|-----------------|
| `h2h_home_win_rate` | Home team win rate in last 10 prior meetings (either side as home) | Only meetings where f.date < target.date |
| `h2h_draw_rate` | Draw rate in last 10 prior meetings | Same |
| `h2h_away_win_rate` | Away team win rate in last 10 prior meetings | Same |
| `h2h_avg_goals` | Average total goals per meeting in last 10 | Same |
| `h2h_weighted_home_win_rate` | Exponentially-weighted home win rate (0.7^k decay, most recent = weight 1) | Same |

**H2H direction:** For each historical meeting, we check whether the current home team was actually the home team in that meeting and label the result accordingly (H/D/A from the current-fixture perspective).

**Default (no prior meetings):** win_rate=0.45, draw_rate=0.27, away_win_rate=0.28, avg_goals=2.5, weighted=0.45.

### Combined feature vector (29 features)

```
[0:9]   Standings-derived baseline (from V3):
          h_rank, a_rank, h_gf-h_ga, a_gf-a_ga, h_gf, a_gf, h_ga, a_ga, |h_rank-a_rank|

[9:15]  F1 home team rolling form (6 features, N=5 or N=10)
[15:21] F1 away team rolling form (6 features)
[21:24] F2 league context (3 features)
[24:29] F3 H2H (5 features)
```

### Verification

```
>>> builder = FeatureBuilder(conn, n_rolling=5)
>>> builder.load()
  Teams: 3,085  Leagues: 1,186  H2H pairs: 235,461
>>> builder.build(sample_fixture).shape
(20,)
>>> builder.build_pair(sample_fixture)  # returns (f5, f10)
```

Unit test: `python scripts/analysis/features_v1.py` completes successfully.

---

## R. Retrain & Walk-Forward Validate

### Status: Complete. **All four markets FAIL the pre-registered success bar.**

**Script:** `scripts/analysis/walk_forward_backtest_v4.py`  
**Features:** 29 total (9 standings baseline + 20 Wave 1)  
**Model:** LightGBM (300 estimators, 31 leaves, lr=0.05) + Platt calibration + Shin-blend (MODEL_WEIGHT=0.35)  
**EV filter:** > 5% (production threshold)  
**Training:** Up to 40,000 fixtures per window (recency-biased subsample); 70/30 fit/calibration split  
**Validation pool:** 20,658 fixtures — 17,629 fdco (2019–2023, h2h + ou25 only) + 3,029 production (2025–2026, all markets)

---

### Backtest integrity note

A label-inversion bug was caught and corrected mid-run. The first pass showed ou25 ROI of +27% to +73% across windows — physically impossible (would require ~67% win rate in a 50/50 market). Root cause: for binary markets, `idx=0` was used as both the Shin de-vigging index (correctly mapping to the "over" probability) and the win condition (`won = (label == idx)`), but the label function returns 1 for "over" and 0 for "under". Platt calibration was therefore trained on inverted labels, effectively predicting P(under) while the EV formula used over-odds — an impossible real-world payout. Fix: added `win_label` field (=1 for all binary positive outcomes: over/yes) separate from `idx`. h2h (multiclass) was unaffected. Final results below are post-fix.

---

### Results — N=5 rolling window (5 prior matches)

**Per-window detail:**

| Market | Window | Bets | Win Rate | Avg Odds | ROI% | 95% CI | ≥500 bets? | CI>0? |
|--------|--------|------|----------|----------|------|--------|-----------|-------|
| h2h | 2022 | 1,590 | 28.2% | 4.175 | -6.8% | [-14.6%, +1.3%] | ✓ | ✗ |
| h2h | 2023 | 3,121 | 26.0% | 4.249 | -9.6% | [-15.4%, -3.9%] | ✓ | ✗ |
| h2h | 2025-26 | 1,960 | 23.6% | 5.144 | -8.0% | [-16.8%, +1.2%] | ✓ | ✗ |
| btts | 2022 | 0 | — | — | — | — | ✗ | ✗ |
| btts | 2023 | 0 | — | — | — | — | ✗ | ✗ |
| btts | 2025-26 | 98 | 45.9% | 2.625 | +17.3% | [-8.3%, +43.9%] | ✗ | ✗ |
| ou25 | 2022 | 55 | 34.5% | 2.372 | -18.5% | [-46.8%, +12.1%] | ✗ | ✗ |
| ou25 | 2023 | 1,288 | 41.5% | 2.292 | -7.2% | [-13.2%, -1.1%] | ✓ | ✗ |
| ou25 | 2025-26 | 225 | 36.9% | 2.557 | -10.1% | [-26.5%, +6.4%] | ✗ | ✗ |
| ou15 | 2022 | 0 | — | — | — | — | ✗ | ✗ |
| ou15 | 2023 | 0 | — | — | — | — | ✗ | ✗ |
| ou15 | 2025-26 | 23 | 47.8% | 1.717 | -18.8% | [-54.0%, +16.6%] | ✗ | ✗ |

**N=5 aggregate across all windows:**

| Market | Bets | Pass% | ROI% | 95% CI | AvgEV | AvgOdds | Win Rate | **Bar** |
|--------|------|-------|------|--------|-------|---------|----------|---------|
| h2h | 6,671 | 18.5% | -8.5% | [-12.9%, -4.1%] | 18.2% | 4.495 | 25.8% | **FAIL** |
| btts | 98 | 0.4% | +17.3% | [-8.3%, +43.9%] | 10.5% | 2.625 | 45.9% | **FAIL** |
| ou25 | 1,568 | 6.5% | -8.0% | [-13.4%, -2.3%] | 9.7% | 2.333 | 40.6% | **FAIL** |
| ou15 | 23 | 0.1% | -18.8% | [-54.0%, +16.6%] | 7.1% | 1.717 | 47.8% | **FAIL** |

---

### Results — N=10 rolling window (10 prior matches)

**Per-window detail:**

| Market | Window | Bets | Win Rate | Avg Odds | ROI% | 95% CI | ≥500 bets? | CI>0? |
|--------|--------|------|----------|----------|------|--------|-----------|-------|
| h2h | 2022 | 1,519 | 26.0% | 4.369 | -10.6% | [-18.8%, -2.0%] | ✓ | ✗ |
| h2h | 2023 | 3,091 | 27.3% | 4.057 | -10.4% | [-16.1%, -4.8%] | ✓ | ✗ |
| h2h | 2025-26 | 1,952 | 24.1% | 5.126 | -7.0% | [-15.6%, +1.9%] | ✓ | ✗ |
| btts | 2022 | 0 | — | — | — | — | ✗ | ✗ |
| btts | 2023 | 0 | — | — | — | — | ✗ | ✗ |
| btts | 2025-26 | 117 | 44.4% | 2.586 | +12.8% | [-9.9%, +36.8%] | ✗ | ✗ |
| ou25 | 2022 | 59 | 25.4% | 2.400 | -39.9% | [-65.3%, -12.8%] | ✗ | ✗ |
| ou25 | 2023 | 1,136 | 39.6% | 2.302 | -11.1% | [-17.5%, -4.5%] | ✓ | ✗ |
| ou25 | 2025-26 | 213 | 35.2% | 2.555 | -14.1% | [-29.9%, +2.7%] | ✗ | ✗ |
| ou15 | 2022 | 0 | — | — | — | — | ✗ | ✗ |
| ou15 | 2023 | 0 | — | — | — | — | ✗ | ✗ |
| ou15 | 2025-26 | 28 | 50.0% | 1.730 | -13.4% | [-46.0%, +19.3%] | ✗ | ✗ |

**N=10 aggregate across all windows:**

| Market | Bets | Pass% | ROI% | 95% CI | AvgEV | AvgOdds | Win Rate | **Bar** |
|--------|------|-------|------|--------|-------|---------|----------|---------|
| h2h | 6,562 | 18.2% | -9.4% | [-13.9%, -5.0%] | 17.8% | 4.447 | 26.1% | **FAIL** |
| btts | 117 | 0.5% | +12.8% | [-9.9%, +36.8%] | 10.8% | 2.586 | 44.4% | **FAIL** |
| ou25 | 1,408 | 5.9% | -12.8% | [-18.8%, -6.5%] | 9.6% | 2.345 | 38.4% | **FAIL** |
| ou15 | 28 | 0.1% | -13.4% | [-46.0%, +19.3%] | 7.7% | 1.730 | 50.0% | **FAIL** |

---

### Comparison to Phase 1d baseline

Phase 1d baseline (9-feature model, OOF blend, all markets combined): ROI **-1.4%** [95% CI: -8.1%, +4.9%], 1,716 bets.

Wave 1 (29 features, per-market LightGBM):

| Market | Phase 1d OOF | V4 N=5 | V4 N=10 | Direction |
|--------|-------------|--------|---------|-----------|
| h2h (aggregate) | -1.4% (mixed) | -8.5% [-12.9%, -4.1%] | -9.4% [-13.9%, -5.0%] | ↓ Worse |
| ou25 (aggregate) | -1.4% (mixed) | -8.0% [-13.4%, -2.3%] | -12.8% [-18.8%, -6.5%] | ↓ Worse |
| btts | N/A (fdco had no btts) | +17.3% (CI includes 0; n=98) | +12.8% (CI includes 0; n=117) | ↔ Underpowered |

*Phase 1d baseline was a single OOF pool across all markets; per-market comparison is approximate.*

Wave 1 features do not improve on the Phase 1d baseline. The h2h and ou25 markets show statistically significant **negative** ROI (CI excludes zero on the downside). This is not a marginal miss — it is a definitive negative result.

---

### Success bar verdict — explicit per market

Pre-registered criteria: **95% CI excludes zero** (positive direction), **≥500 bets per window**, **holds across ≥2 non-overlapping windows**.

| Market | N=5 verdict | N=10 verdict | Why |
|--------|------------|-------------|-----|
| h2h | **FAIL** | **FAIL** | Meets bet floor in all 3 windows; CI consistently negative (upper bound +1–2% at best) |
| btts | **FAIL** | **FAIL** | Zero fdco btts odds; only 1 production window; 98–117 bets (far below 500-bet floor) |
| ou25 | **FAIL** | **FAIL** | Only 1 window clears 500-bet floor (2023); CI negative in that window (-13.2% / -17.5%); other windows below floor |
| ou15 | **FAIL** | **FAIL** | Zero fdco ou15 odds; 23–28 bets in production window (far below floor); as expected pre-run |

**0 of 8 market × N-value combinations pass.** No market passes the success bar at either N=5 or N=10.

---

### Observations

1. **h2h active deterioration.** The h2h market ROI of -8.5% to -9.4% is significantly worse than Phase 1d. High AvgEV (18%) with poor realized ROI indicates the EV filter is selecting bets where the model is systematically over-confident (calibration gap between training and test). The h2h market has 3 outcomes and very high odds (AvgOdds ~4.4–5.1), making it extremely difficult to beat.

2. **ou25 negative with high confidence.** ou25 ROI of -8.0% (N=5) and -12.8% (N=10) with CI fully negative. Wave 1 rolling form features appear to provide no edge in the over/under market beyond what is already priced by bookmakers.

3. **btts underpowered by design.** btts odds are only available in the production window (fdco has no btts). 2 of 3 test windows have zero bets. Cannot evaluate — not a modelling failure, a data coverage limitation.

4. **ou15 as expected.** Zero bets in fdco windows (no ou15 odds), 23–28 bets in production window. Fails the bet-count floor regardless of ROI, as projected pre-run in Task P.

5. **N=5 vs N=10.** N=5 marginally outperforms N=10 in h2h and ou25 (less negative ROI). Shorter rolling windows may be less susceptible to regime-change noise at the cost of higher variance. Neither N value passes the bar.

6. **Trainer.py architecture discrepancy (flagged).** Production `src/models/trainer.py` trains `sklearn.GradientBoostingClassifier`; the V4 backtest uses `lgb.LGBMClassifier`. These are different algorithms. Given that all markets fail here, there is no validated model to deploy — this discrepancy does not need immediate resolution, but should be aligned before any future production update.

---

---

## S. Versioning Infrastructure

### Status: Complete. 5/5 unit tests pass.

### Changes made

**Migration 025 (`migrations/025_versioning_tuple.sql`):**
```sql
ALTER TABLE prediction_records ADD COLUMN blend_version VARCHAR(20);
```
Applied to production DB.

**`src/storage/models.py`:** Added `blend_version` column to `PredictionRecord` SQLAlchemy model.

**`src/prediction/unified_prediction_service.py`:**
- Added module-level constants:
  ```python
  FEATURE_PIPELINE_VERSION = "v1.0.0"   # bump to v2.0.0 when Wave 1 deploys
  BLEND_VERSION            = "v1.0"     # MODEL_WEIGHT=0.35, Shin de-vigging
  ```
- Added both to the prediction dict in `generate_with_fixture_data()`:
  ```python
  "feature_pipeline_version": FEATURE_PIPELINE_VERSION,
  "blend_version": BLEND_VERSION if (has_odds and p_market is not None) else None,
  ```
- Added storage in `save_predictions()`:
  ```python
  if pred.get("feature_pipeline_version"):
      record.feature_pipeline_version = pred["feature_pipeline_version"]
  if pred.get("blend_version") is not None or "blend_version" in pred:
      record.blend_version = pred.get("blend_version")
  ```

### Full 4-tuple in prediction_records

| Column | Type | Description | When set |
|--------|------|-------------|----------|
| `feature_pipeline_version` | VARCHAR(20) default 'v1.0.0' | Feature set version | Every prediction |
| `model_version_id` | INTEGER FK | Points to model_versions row | Every prediction (via model_version_cache) |
| `calibration_version_id` | VARCHAR(50) | League calibration version | When calibration applied |
| `blend_version` | VARCHAR(20) NULL | Shin-blend formula version | 'v1.0' when blend applied; NULL if no odds or fallback |

### Unit test results

```
Versioning tuple tests
========================================
test_versioning_constants:         FEATURE_PIPELINE_VERSION='v1.0.0' ✓  BLEND_VERSION='v1.0' ✓
test_prediction_dict_includes_versions:  feature_pipeline_version='v1.0.0' ✓  blend_version='v1.0' ✓
test_blend_version_null_when_no_market_odds:  blend_version=None when p_market is None ✓
test_db_column_exists:             All 4 columns in prediction_records ✓
test_sqlalchemy_model_has_blend_version:  PredictionRecord.blend_version ✓

Result: 5/5 passed
```

**Live population caveat:** The pipeline was inactive as of 2026-06-08. The versioning code path has been implemented and unit-tested, but live population on new predictions has NOT been verified end-to-end (that would require the pipeline to run and produce new predictions). If/when the pipeline resumes, the first prediction record should show `feature_pipeline_version='v1.0.0'` and `blend_version='v1.0'` (or NULL if the prediction has no odds available for blending).

---

## Summary

| Task | Status | Key finding |
|------|--------|------------|
| O | Complete | 35,256 fdco rows moved from production to `historical_odds.db`; production DB clean; ATTACH pattern verified |
| P | Complete | 20,658-fixture validation pool; 87.5% have full F1 features; 3 non-overlapping windows each with 3,000–3,580 fixtures |
| Q | Complete | 20 new Wave 1 features (12 F1 rolling form + 3 F2 league context + 5 F3 H2H) in `features_v1.py`; combined with 9 baseline = 29-dim vector; all leakage boundaries documented and verified |
| R | Complete | **All 4 markets FAIL** the pre-registered success bar at both N=5 and N=10. h2h: -8.5% to -9.4% ROI (CI negative). ou25: -8.0% to -12.8% (CI negative). btts/ou15: insufficient bet volume. Wave 1 features do not provide a detectable edge. |
| S | Complete | `blend_version` column added to DB and ORM; all 4 versioning fields now written explicitly per prediction; 5/5 unit tests pass; live population untested (pipeline inactive) |

---

## Diagnostic Addendum: V1 / V2 / V3

*Appended after Task R. Pre-registered success bar from Task R applies.*

*Ground rules: read-only production schema; all analysis in scripts/analysis/.*

### V2 Hyperparameter Finding (code read)

| Model | n_estimators | num_leaves | learning_rate | Features |
|-------|-------------|------------|--------------|----------|
| V3 baseline | 200 | 31 | 0.05 | 9 (standings) |
| V4 Wave 1 | 300 | 31 | 0.05 | 29 (standings + Wave1) |

V4 bumped `n_estimators` from 200→300 but kept `num_leaves=31`.
With 3× more features, the same tree complexity likely underfits —
this is a plausible confound. Grid below tests `num_leaves` ∈ {31, 63, 127}
and `n_estimators` ∈ {300, 500}.

### V1a: Raw Classifier Metrics (test set, no market blend)

Metrics from raw model probabilities — before Platt calibration and before blending.
Lower log-loss/Brier = better; higher AUC = better.

**H2H**

| Window | Variant | AUC | Log-Loss | Brier |
|--------|---------|-----|----------|-------|
| 2022 | 9feat | 0.5841 | 1.1654 | 0.2310 |
| 2022 | 29feat_n5 | 0.5842 | 1.1132 | 0.2219 |
| 2022 | 29feat_n10 | 0.5870 | 1.1054 | 0.2192 |
| 2023 | 9feat | 0.5784 | 1.1490 | 0.2285 |
| 2023 | 29feat_n5 | 0.5890 | 1.1070 | 0.2214 |
| 2023 | 29feat_n10 | 0.5840 | 1.1094 | 0.2212 |
| 2025-26 | 9feat | 0.5620 | 1.1024 | 0.2203 |
| 2025-26 | 29feat_n5 | 0.5799 | 1.0755 | 0.2152 |
| 2025-26 | 29feat_n10 | 0.5898 | 1.0669 | 0.2137 |

**OU25**

| Window | Variant | AUC | Log-Loss | Brier |
|--------|---------|-----|----------|-------|
| 2022 | 9feat | 0.4963 | 0.8105 | 0.2949 |
| 2022 | 29feat_n5 | 0.5096 | 0.7626 | 0.2778 |
| 2022 | 29feat_n10 | 0.5083 | 0.7722 | 0.2812 |
| 2023 | 9feat | 0.5113 | 0.7688 | 0.2822 |
| 2023 | 29feat_n5 | 0.5437 | 0.7096 | 0.2570 |
| 2023 | 29feat_n10 | 0.5368 | 0.7098 | 0.2572 |
| 2025-26 | 9feat | 0.4640 | 0.7474 | 0.2746 |
| 2025-26 | 29feat_n5 | 0.5642 | 0.6998 | 0.2527 |
| 2025-26 | 29feat_n10 | 0.5699 | 0.6983 | 0.2519 |

**BTTS**

| Window | Variant | AUC | Log-Loss | Brier |
|--------|---------|-----|----------|-------|
| 2022 | 9feat | 0.4876 | 0.7613 | 0.2795 |
| 2022 | 29feat_n5 | 0.5120 | 0.7338 | 0.2678 |
| 2022 | 29feat_n10 | 0.5105 | 0.7324 | 0.2674 |
| 2023 | 9feat | 0.5125 | 0.7526 | 0.2745 |
| 2023 | 29feat_n5 | 0.5193 | 0.7285 | 0.2653 |
| 2023 | 29feat_n10 | 0.5295 | 0.7263 | 0.2644 |
| 2025-26 | 9feat | 0.4796 | 0.7375 | 0.2697 |
| 2025-26 | 29feat_n5 | 0.5306 | 0.7117 | 0.2578 |
| 2025-26 | 29feat_n10 | 0.5206 | 0.7120 | 0.2581 |

**OU15**

| Window | Variant | AUC | Log-Loss | Brier |
|--------|---------|-----|----------|-------|
| 2022 | 9feat | 0.5092 | 0.6619 | 0.2291 |
| 2022 | 29feat_n5 | 0.5256 | 0.6456 | 0.2243 |
| 2022 | 29feat_n10 | 0.5289 | 0.6478 | 0.2262 |
| 2023 | 9feat | 0.4989 | 0.7509 | 0.2293 |
| 2023 | 29feat_n5 | 0.5152 | 0.6111 | 0.2052 |
| 2023 | 29feat_n10 | 0.5335 | 0.6135 | 0.2047 |
| 2025-26 | 9feat | 0.4826 | 0.5881 | 0.1974 |
| 2025-26 | 29feat_n5 | 0.5697 | 0.5559 | 0.1847 |
| 2025-26 | 29feat_n10 | 0.5706 | 0.5571 | 0.1853 |

### V1b: Blend Weight Re-Optimization (29-feat N=5)

Grid: model weights [0%, 15%, 25%, 35%, 50%, 65%, 100%].
Selected on cal holdout only (note: same holdout used for Platt → mild optimism).

**H2H**

| Window | Outcome | Best Weight | Default (0.35) LL | Best LL | Bets(default) | ROI(default) | Bets(opt) | ROI(opt) |
|--------|---------|------------|-------------------|---------|--------------|-------------|----------|---------|
| 2022 | H | 35% | n/a | n/a | 1590 | -6.8% | 1590 | -6.8% |
| 2022 | D | 35% | n/a | n/a | 1590 | -6.8% | 1590 | -6.8% |
| 2022 | A | 35% | n/a | n/a | 1590 | -6.8% | 1590 | -6.8% |
| 2023 | H | 35% | n/a | n/a | 3121 | -9.6% | 3121 | -9.6% |
| 2023 | D | 35% | n/a | n/a | 3121 | -9.6% | 3121 | -9.6% |
| 2023 | A | 35% | n/a | n/a | 3121 | -9.6% | 3121 | -9.6% |
| 2025-26 | H | 35% | n/a | n/a | 1960 | -8.0% | 1960 | -8.0% |
| 2025-26 | D | 35% | n/a | n/a | 1960 | -8.0% | 1960 | -8.0% |
| 2025-26 | A | 35% | n/a | n/a | 1960 | -8.0% | 1960 | -8.0% |

**OU25**

| Window | Outcome | Best Weight | Default (0.35) LL | Best LL | Bets(default) | ROI(default) | Bets(opt) | ROI(opt) |
|--------|---------|------------|-------------------|---------|--------------|-------------|----------|---------|
| 2022 | over | 35% | n/a | n/a | 55 | -18.5% | 55 | -18.5% |
| 2023 | over | 35% | n/a | n/a | 1288 | -7.2% | 1288 | -7.2% |
| 2025-26 | over | 35% | n/a | n/a | 225 | -10.1% | 225 | -10.1% |

**BTTS**

| Window | Outcome | Best Weight | Default (0.35) LL | Best LL | Bets(default) | ROI(default) | Bets(opt) | ROI(opt) |
|--------|---------|------------|-------------------|---------|--------------|-------------|----------|---------|
| 2022 | yes | 35% | n/a | n/a | 0 | n/a | 0 | n/a |
| 2023 | yes | 35% | n/a | n/a | 0 | n/a | 0 | n/a |
| 2025-26 | yes | 35% | n/a | n/a | 98 | +17.3% | 98 | +17.3% |

### V2: Hyperparameter Grid Search Results (29-feat N=5)

Selected by cal-holdout log-loss. Tuned EV uses best HP with MODEL_WEIGHT=0.35.

**H2H**

| Window | Best HP | Cal-LL (default nl31/ne300) | Cal-LL (best) | Bets(default) | ROI(default) | Bets(tuned) | ROI(tuned) | CI(tuned) |
|--------|---------|--------------------------|---------------|--------------|-------------|------------|-----------|----------|
| 2022 | nl31/ne300 | 1.0443 | 1.0443 | 1590 | -6.8% | 1590 | -6.8% | [-14.6%, +1.3%] |
| 2023 | nl31/ne300 | 1.0168 | 1.0168 | 3121 | -9.6% | 3121 | -9.6% | [-15.4%, -3.9%] |
| 2025-26 | nl31/ne300 | 1.0136 | 1.0136 | 1960 | -8.0% | 1960 | -8.0% | [-16.8%, +1.2%] |

**OU25**

| Window | Best HP | Cal-LL (default nl31/ne300) | Cal-LL (best) | Bets(default) | ROI(default) | Bets(tuned) | ROI(tuned) | CI(tuned) |
|--------|---------|--------------------------|---------------|--------------|-------------|------------|-----------|----------|
| 2022 | nl31/ne300 | 0.6931 | 0.6931 | 55 | -18.5% | 55 | -18.5% | [-46.8%, +12.1%] |
| 2023 | nl31/ne300 | 0.6714 | 0.6714 | 1288 | -7.2% | 1288 | -7.2% | [-13.2%, -1.1%] |
| 2025-26 | nl31/ne300 | 0.6775 | 0.6775 | 225 | -10.1% | 225 | -10.1% | [-26.5%, +6.4%] |

### V3: Rolling-Feature Reliability (29-feat N=5)

Validation pool: 20,658 fixtures.

| Threshold | Cold-start fixtures | Fraction |
|-----------|-------------------|---------|
| < 5 prior matches | 2,535 | 12.3% |
| < 10 prior matches | 3,175 | 15.4% |

**Cold-start vs warm bet ROI (all windows combined, 29-feat N=5):**

| Market | Threshold | Group | N bets | Avg ROI | CI |
|--------|-----------|-------|--------|---------|-----|
| h2h | 5 | ≥5 prior | 5,664 | -8.7% | (per-window CI above) |
| h2h | 5 | <5 prior | 1,007 | -7.4% | (per-window CI above) |
| h2h | 10 | ≥10 prior | 5,604 | -8.7% | (per-window CI above) |
| h2h | 10 | <10 prior | 1,067 | -7.5% | (per-window CI above) |
| ou25 | 5 | ≥5 prior | 1,451 | -7.9% | (per-window CI above) |
| ou25 | 5 | <5 prior | 117 | -10.1% | (per-window CI above) |
| ou25 | 10 | ≥10 prior | 1,436 | -7.5% | (per-window CI above) |
| ou25 | 10 | <10 prior | 132 | -13.9% | (per-window CI above) |
| btts | 5 | ≥5 prior | 31 | +13.7% | (per-window CI above) |
| btts | 5 | <5 prior | 67 | +19.0% | (per-window CI above) |
| btts | 10 | ≥10 prior | 27 | +12.3% | (per-window CI above) |
| btts | 10 | <10 prior | 71 | +19.2% | (per-window CI above) |

### Diagnostic Conclusions

*(Auto-generated from run results — interpret in context of Task R verdicts.)*

- **V1a (Raw Classifier):** See table above. If Wave1 AUC ≤ 9-feat AUC, the Wave1 feature set provides no classifier improvement and the negative EV outcome is explained at the signal level, not the blending/HP level.
- **V1b (Blend Weight):** See table above. If best_weight ≠ 0.35 and ROI(opt) materially differs from ROI(default), blend weight was a confound.
- **V2 (HP Tuning):** See table above. If ROI(tuned) with wider num_leaves still shows negative CI, HP underfitting was not the primary cause of failure.
- **V3 (Cold-Start):** See fraction table above. If cold-start fixtures have significantly worse ROI than warm fixtures, fixture_stats data coverage is a confound for Wave1 rolling features.

### V1b (Corrected): Blend Weight Re-Optimization — Pre-Window Val_Fix

Selection pool: val_fix[date < test_start] (has odds, no test leakage).

Platt calibration uses cal holdout (same as main pipeline).


**H2H**

| Window | Best Weight | N sel | ROI(w=0%) | ROI(w=15%) | ROI(w=25%) | ROI(w=35%) | ROI(w=50%) | ROI(w=65%) | ROI(w=100%) |
|--------|------------|-------|-----------|-----------|-----------|-----------|-----------|-----------|------------|
| 2022 | **100%** | 8,632 | 0 bets | -2.4% (n=347) | -8.5% (n=1003) | -6.8% (n=1590) | -9.2% (n=2205) | -8.0% (n=2671) | -8.9% (n=3278) |
| 2023 | **65%** | 12,172 | 0 bets | -12.7% (n=1038) | -11.8% (n=2243) | -9.6% (n=3121) | -9.2% (n=4028) | -8.2% (n=4637) | -8.3% (n=5409) |
| 2025-26 | **25%** | 17,628 | -12.1% (n=34) | -1.0% (n=855) | -6.9% (n=1498) | -8.0% (n=1960) | -8.2% (n=2468) | -9.6% (n=2773) | -10.5% (n=3231) |

**OU25**

| Window | Best Weight | N sel | ROI(w=0%) | ROI(w=15%) | ROI(w=25%) | ROI(w=35%) | ROI(w=50%) | ROI(w=65%) | ROI(w=100%) |
|--------|------------|-------|-----------|-----------|-----------|-----------|-----------|-----------|------------|
| 2022 | **100%** | 8,632 | 0 bets | 0 bets | -2.7% (n=16) | -18.5% (n=55) | -23.1% (n=152) | -17.8% (n=262) | -10.6% (n=420) |
| 2023 | **65%** | 12,172 | 0 bets | -21.3% (n=73) | -12.8% (n=616) | -7.2% (n=1288) | -8.4% (n=2056) | -6.6% (n=2524) | -6.5% (n=3095) |
| 2025-26 | **25%** | 17,628 | -100.0% (n=1) | -30.8% (n=16) | -16.6% (n=100) | -10.1% (n=225) | -8.9% (n=382) | -7.4% (n=494) | -4.2% (n=680) |

**Key finding:** See whether best_weight ≠ 0.35 and whether optimized EV
is materially different from default w=0.35.

---

## Diagnostic Synthesis: V1 / V2 / V3 Conclusions

*Completes the tuning-pass brief. Pre-registered success bar from Task R applies to all EV tests below.*

### Summary Table

| Diagnostic | Question | Finding | Changes verdict? |
|-----------|---------|---------|----------------|
| V1a: Raw classifier | Does Wave1 improve over 9-feat? | h2h AUC +0.010; ou25 AUC +0.049. Wave1 consistently better but margin small. | No — improvement insufficient to generate positive EV |
| V1b: Blend weight | Is w=0.35 suboptimal? | Best weight unstable: 100% (2022), 65% (2023), 25% (2025-26) for h2h. All windows FAIL at optimized weight. | No |
| V2: HP tuning | Is nl=31 underfitting 29 features? | nl=31, ne=300 wins all 6 cal-holdout comparisons. Larger trees (nl=63, 127) overfit the 40K training cap. | No |
| V3: Cold-start | Does form-data sparsity explain losses? | 12.3% fixtures have <5 prior matches; ROI warm ≈ cold (-8.7% vs -7.4% for h2h). | No |

### V1a: Wave1 Classifier Quality (AUC, test window)

| Window | Market | 9-feat AUC | 29-feat-N5 AUC | Δ |
|--------|--------|-----------|----------------|--|
| 2022 | h2h | 0.5841 | 0.5842 | +0.0001 |
| 2022 | ou25 | 0.4963 | 0.5096 | +0.0133 |
| 2023 | h2h | 0.5784 | 0.5890 | +0.0106 |
| 2023 | ou25 | 0.5113 | 0.5437 | +0.0324 |
| 2025-26 | h2h | 0.5620 | 0.5799 | +0.0179 |
| 2025-26 | ou25 | 0.4640 | 0.5642 | +0.1002 |

Wave1 rolling features improve ou25 AUC substantially (+0.049 avg) and h2h modestly (+0.010 avg). The signal is real. The issue is that AUC of 0.58 (h2h) and 0.54 (ou25) is insufficient to reliably identify 5%+ EV opportunities after Platt calibration and bookmaker margin. No market reached the Phase 2 success bar at any blend weight.

### V1b: Blend Weight Grid (29-feat N=5, optimized on pre-window val_fix)

**h2h**
| Window | Best w | Bets(0.35) | ROI(0.35) | Bets(best) | ROI(best) | 95% CI |
|--------|-------|-----------|----------|-----------|----------|--------|
| 2022 | 100% | 1,590 | -6.8% | 3,278 | -8.9% | [-14.4%, -3.3%] |
| 2023 | 65% | 3,121 | -9.6% | 4,637 | -8.2% | [-12.8%, -3.6%] |
| 2025-26 | 25% | 1,960 | -8.0% | 1,498 | -6.9% | [-17.7%, +4.5%] |

**ou25**
| Window | Best w | Bets(0.35) | ROI(0.35) | Bets(best) | ROI(best) | 95% CI |
|--------|-------|-----------|----------|-----------|----------|--------|
| 2022 | 100% | 55 | -18.5% | 420 | -10.6% | [-21.5%, -0.8%] |
| 2023 | 65% | 1,288 | -7.2% | 2,524 | -6.6% | [-10.7%, -2.2%] |
| 2025-26 | 25% | 225 | -10.1% | 100 | -16.6% | [-40.8%, +7.5%] |

The optimal weight varies widely across windows (100% → 65% → 25%). This instability means blend weight re-optimization would require frequent re-fitting and does not produce a consistently profitable strategy. All six (window × market) combinations FAIL the success bar at their respective optimized weights.

Note: The pre-window val_fix used for weight selection grew from 8.6K (2022) to 17.6K (2025-26) fixtures — adequate for meaningful selection.

### V2: HP Grid Results (29-feat N=5, cal-holdout log-loss)

Default HP (nl=31, ne=300) wins in all 6 tested window-market combinations. Cal-holdout LL by HP, representative window (2023/h2h):

| HP | Cal-LL | vs default |
|----|--------|-----------|
| nl=31, ne=300 (default) | 1.0168 | — |
| nl=63, ne=300 | 1.0246 | +0.0078 worse |
| nl=127, ne=300 | 1.0452 | +0.0284 worse |
| nl=31, ne=500 | 1.0256 | +0.0088 worse |
| nl=63, ne=500 | 1.0396 | +0.0228 worse |
| nl=127, ne=500 | 1.0728 | +0.0560 worse |

Wider trees overfit — the 40K training cap is the binding constraint, not tree width. At 40K samples × 29 features, nl=31 is the appropriate complexity level. **HP underfitting is not a confound.**

### V3: Rolling-Feature Reliability

| Threshold | Cold-start fixtures | Fraction of val pool |
|-----------|-------------------|---------------------|
| < 5 prior matches | 2,535 of 20,658 | 12.3% |
| < 10 prior matches | 3,175 of 20,658 | 15.4% |

**Cold vs warm ROI (29-feat N=5, all windows pooled):**

| Market | Group | N bets | ROI |
|--------|-------|--------|-----|
| h2h | warm (≥5 prior) | 5,664 | -8.7% |
| h2h | cold (<5 prior) | 1,007 | -7.4% |
| ou25 | warm (≥5 prior) | 1,451 | -7.9% |
| ou25 | cold (<5 prior) | 117 | -10.1% |

Warm and cold bets perform similarly. Cold-start fixtures do not disproportionately explain the negative ROI. **Data sparsity is not a confound.**

Note: The 2025-26 window shows ~50% cold-start bets for h2h (985 of 1,960) — fixture_stats coverage for the live production season is sparse. This is a data-pipeline observation for future Wave 1 deployment, not a backtest confound.

### Overall Conclusion

**Approach A (Wave 1 rolling features) is exhausted.** The diagnostic pass rules out the three most plausible confounds:

1. HP underfitting — **ruled out** (default HP is optimal across all windows)
2. Blend weight choice — **ruled out** (no weight produces positive-CI EV; optimized weight is unstable across windows)
3. Cold-start data sparsity — **ruled out** (warm ≈ cold ROI in all markets)

The classifier improvement from Wave1 is real (ou25 AUC +0.049, h2h +0.010) but too small to overcome the bookmaker margin at the 5% EV threshold. The failure mode is signal strength, not implementation quality. Proceeding to Approach B or redesigning the feature strategy is warranted.

---
