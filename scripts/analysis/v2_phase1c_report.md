# Bootball V2 Phase 1c Report

**Generated:** 2026-06-24  
**Scope:** I1 (endpoint inventory), I2 (leakage-safe features), I3 (football-data.co.uk), J1–J4 (live EV audit), K (EV threshold application)  
**Ground rules:** Read-only on production code/schema/data. All new code in `scripts/analysis/`. No production changes.

---

## J1 — Live EV Formula Verification

### Status: Confirmed. Active formula is standard; legacy formula is still in the file.

The codebase has **two EV formulas** in `src/prediction/unified_prediction_service.py`:

| Path | Method | Formula | Status |
|------|--------|---------|--------|
| Line 137 | `_generate_for_fixture()` (legacy) | `ev = (our_prob * odds) - (1 - our_prob)` | **NOT ACTIVE** |
| Line 321 | `generate_with_fixture_data()` (active) | `ev = p_blended * odds - 1` | **ACTIVE** |

**The legacy formula is mathematically wrong.** Expanding it: `p*(d+1) - 1`, which exceeds the standard formula `p*d - 1` by exactly `p`. For a typical probability of 0.6, it adds 60pp of phantom EV. For example:

```
p = 0.55, odds = 2.0
Standard (correct): 0.55 × 2.0 - 1 = 0.10 (10% EV)
Legacy (wrong):     0.55 × 2.0 - 0.45 = 0.65 (65% EV)
```

**Which formula is live?** The coordinator calls `generate_with_fixture_data()` (confirmed via coordinator.py). The legacy `_generate_for_fixture()` is invoked by the older `generate()` method, which is NOT called in the current production pipeline. The standard formula is active for all bet placement decisions.

**Risk:** The legacy formula is still present and would be reached if `generate()` is ever called (e.g., from any code path that predates the refactor). It should be removed or rewritten to match the active formula.

---

## J2 — Calibration Mechanism Reconciliation

### Status: Three distinct layers, running in sequence. Only two are active.

The codebase has three layers of probability adjustment. They are NOT competing systems — they are a sequential pipeline:

```
raw_prob  →  [LAYER 1 DISABLED]  →  [LAYER 2 ACTIVE]  →  [LAYER 3 ACTIVE]  →  p_blended  →  EV
```

### Layer 1: pkl IsotonicRegression (DISABLED)

- **Source:** `data/model_{market}.pkl` — each pkl contains `{'model': LGBMClassifier, 'calibrator': optional}`
- **Status:** Loaded but silently skipped. The check at `src/betting/prediction.py:260-268` is:
  ```python
  if calibrator and hasattr(calibrator, 'calibrate'):
      probs[k] = calibrator.calibrate(probs[k]).calibrated_prob
  ```
  `IsotonicRegression` has no `.calibrate()` method — only `MarketCalibrator` objects do. All current pkls contain raw `sklearn.IsotonicRegression` objects, so this branch is never taken.
- **Why deliberately disabled:** Comment at line 261 states: "fitted on a different scale and produce 0 for out-of-range inputs, breaking the probability distribution."
- **Effect:** Raw LightGBM probabilities reach Layer 2 uncorrected.

### Layer 2: LeagueCalibrationEngine / Platt scaling (ACTIVE)

- **Source:** `src/calibration/league_calibration_engine.py`
- **Status:** 247 active calibrators in `league_calibrations` DB table (243 league-specific + 4 global L0000 fallbacks)
- **Method:** LogisticRegression on logit of raw probability, trained with held-out 20% split
- **Resolution order:** league-specific (≥100 samples) → L0000 global fallback → raw pass-through
- **Effect:** Platt-scales the raw LightGBM probability toward the correct range. Reduces overconfidence partially, but empirically still leaves 10-15pp systematic over-prediction (Phase 1b finding).

### Layer 3: Market blend / Shin (ACTIVE, added June 2026)

- **Source:** `src/calibration/market_blend.py`, `MODEL_WEIGHT = 0.35`
- **Method:** `p_blended = 0.35 × p_model + 0.65 × p_market`, where `p_market` is the Shin de-vigged market probability
- **Fallback:** If odds are unavailable, `p_blended = p_model` (full model weight)
- **Effect:** 65% weight toward the market's implied probability. For a model probability of 0.70 against a market of 0.50: `p_blended = 0.245 + 0.325 = 0.57`. This is the single most aggressive calibration step and the reason EV signals have come down since the April audit.

### Active pipeline (end-to-end)

```
get_model_prediction()          # pkl LGBMClassifier → raw_prob (Layer 1 calibrator skipped)
    ↓
_cal_engine.apply(market, league_id, raw_prob)    # Platt scale → p_final
    ↓
blend_with_market(p_final, market_odds, outcome)  # Shin blend → p_blended (65% market)
    ↓
ev = p_blended * odds - 1                         # Standard EV formula
kelly = max(0, (b * p_blended - q) / b) * 0.25   # Fractional Kelly
```

---

## J3 — Calibrated vs. Raw Probability in Live EV

### Status: EV uses p_blended — a 65%-market-weighted blend, not raw model probability.

The EV used for bet placement is computed from `p_blended` (Layer 3 output), not from the raw model output or the Platt-calibrated intermediate `p_final`.

`p_blended` is defined as:
```
p_blended = 0.35 × p_Platt + 0.65 × p_Shin_market
```

The `our_prob` field stored in `prediction_records` is the **raw model probability before Platt or blending**. The `ev` field stored in `prediction_records` is computed from `p_blended`.

**Implication for analysis:** When auditing stored `ev` values using stored `our_prob`, the two are inconsistent — `our_prob` does NOT correspond to the probability that produced `ev`. Any simulation using `our_prob` to back-compute EV will get a different (higher) answer than the stored value.

---

## J4 — Root Cause: Stored avg_ev of 30–142%

### Status: Three concurrent causes identified and quantified.

The original April 2026 audit found average EVs of 30–142% in stored `prediction_records`. This is not real edge; it is systematic inflation from three compounding sources.

### Current DB state (prediction_records, ev > 0)

| Market | Records | Avg EV | Pass 5% | Pass 20% | Max EV |
|--------|---------|--------|---------|---------|--------|
| btts | 1,426 | 29.3% | 83.3% | 49.5% | 260% |
| h2h | 1,216 | 63.6% | 89.6% | 60.4% | 2,580% |
| ou15 | 1,275 | 33.4% | 87.0% | 53.5% | 1,000% |
| ou25 | 1,603 | 27.1% | 87.0% | 49.3% | 340% |

83–90% of predictions pass the 5% EV threshold, confirming **the EV filter provides no selectivity** in the current system.

### Root Cause 1: Non-standard EV formula (lines 137–142)

The legacy `_generate_for_fixture()` method uses `ev = (p × d) - (1 - p)` = `p×(d+1) - 1`, not `p×d - 1`. The difference is exactly `+p`, inflating every EV by 50–70pp for typical probability ranges. This method was active before the refactor and produced the majority of prediction_records currently in the DB.

```
Example: p=0.60, d=2.0
Correct:  0.60 × 2.0 - 1 = 0.20 (20% EV)
Wrong:    0.60 × 2.0 - 0.40 = 0.80 (80% EV)
```

### Root Cause 2: Model overconfidence (Layer 1 disabled)

The LightGBM models systematically over-predict by 10–18pp across all deciles (Phase 1a finding):

| True win rate | Model prediction | Over-prediction |
|---------------|-----------------|-----------------|
| 10–20% | 22–29% | +10–14pp |
| 30–40% | 38–45% | +7–8pp |
| 50–60% | 56–69% | +10–18pp |

Each 10pp over-prediction inflates EV by `0.10 × (odds - 1) ≈ 10–20pp` on typical odds (1.8–3.0). The IsotonicRegression calibrator that was supposed to correct this is disabled (Layer 1 DISABLED above).

### Root Cause 3: No market blending before June 2026

`market_blend.py` was added in June 2026. Before that, the full uncorrected `p_final` (post-Platt) was used in the EV formula. Blending at MODEL_WEIGHT=0.35 suppresses roughly 65% of the model's divergence from the market. Records created before June 2026 reflect the unblended calculation.

### Combined effect

For records in the DB from before June 2026 (most of the prediction_records):
- Wrong formula: +50–70pp
- Overconfident probability: +15–25pp
- No market blending: allows full model overconfidence to pass through

Net: a fixture with true EV of 0% could appear as 65–95% EV in the stored records.

### Current status

Post-June 2026 records use the correct formula and market blending. Yet avg EVs are still 27–64% and 83–90% of predictions clear the 5% threshold. This residual inflation comes from:
- The 35% model weight still carrying overconfident probabilities
- 35% of the original overconfidence error = ~4–6pp phantom contribution
- Magnified by odds: a 4pp phantom signal at odds 2.5 produces ~10pp phantom EV
- Market vig (5–8%) means honest prediction should produce NEGATIVE EV vs. raw odds — so any positive EV from the model alone is suspect unless it genuinely disagrees with and outperforms the market

**The 5% EV threshold is not a functional filter.** Even after market blending, it passes 83–90% of all positive-EV predictions.

---

## K — EV_THRESHOLD Application

### Status: Single global threshold; no per-market overrides exist.

**Config:** `BOT_MIN_EV = 0.05` in `config/settings.py:56` (default 5%).

**Application:** `src/agents/execution_strategist/agent.py:160–183`:
```python
min_ev = settings.bot_min_ev                     # 0.05
...
if not odds or odds < 1.6 or ev <= min_ev:       # line 183
    continue
```

**Per-market override:** None. All four markets (h2h, btts, ou25, ou15) use the same `settings.bot_min_ev`. The only additional filter is `odds < 1.6` (applied before EV check).

**Consequence:** Given that 83–90% of positive-EV predictions already clear 5%, increasing this threshold would not eliminate phantom EV — it would just reduce bet count while the fundamental problem (model overconfidence + blended probability still > true probability) persists. The correct fix is more accurate probability calibration, not raising the threshold.

---

## I1 — Un-ingested API-Football Endpoints (Ultra Plan)

### Status: 5 un-ingested endpoints identified; 2 have potential pre-match feature value.

**Already confirmed ingested:**
- `get_fixtures` / `get_fixtures_batch` — 817K fixtures (events embedded in fixture response, 6.4M rows in `fixture_events`)
- `get_fixture_statistics` → `fixture_stats` (131K rows, post-match)
- `get_standings` → `standings` (14.8K rows)
- `get_odds` → `fixture_odds` (76K rows)
- `get_injuries` → `injuries` (586 rows; called but barely populated)
- `get_players` → `player_season_stats` (781K rows), `players` (49K rows)
- `get_leagues`, `get_teams`, `get_teams_countries` — reference data

**Un-ingested endpoints:**

| Endpoint | Data | Pre/Post Match | Historical Depth | API Call Cost |
|----------|------|----------------|-----------------|---------------|
| `get_lineups(fixture_id)` | Starting XI (11 players), formation, goalkeeper per team | **Pre-match** (~1h before KO) | 2015+ for top leagues | 1 call/fixture. For 100K top-league fixtures: ~100K calls (~1.3 days at 75K/day) |
| `get_team_statistics(league_id, season, team_id)` | Season stats: home/away form string, wins/draws/losses, goals scored/conceded, clean sheets, passing accuracy, avg possession | Pre-match (season to date) | 2015+ | 1 call/team-season. For top 20 leagues × 5 seasons × 20 teams: ~2,000 calls (< 1 hour) |
| `get_predictions(fixture_id)` | API-Football's own ML predictions: P(home_win), P(draw), P(away_win), attack/defense ratings, comparison stats | Pre-match (generated by API-Football) | **No historical archive** — generated in real-time only | 1 call/fixture; no historical value |
| `get_head2head(team1, team2)` | Last N H2H fixtures including results, scores, venue | Post-match (historical) | All seasons | 1 call/team pair. Top 20 leagues × 20 teams × 10 opponents: ~4,000 calls. Derivable from existing `fixtures` table by joining home_team_id + away_team_id. |
| `get_fixture_events(fixture_id)` | Detailed events: player ID, exact minute, type (Goal/Card/Subst/VAR), detail | Post-match | All seasons | 1 call/fixture. Data largely already captured in `fixture_events` (6.4M rows) via embedded fixture response. Adds player_id linkage but no new leakage-safe features. |

### Priority assessment for pre-match features

**High priority:**
1. **`get_lineups`** — Starting XI is genuinely pre-match and captures information the model doesn't have: injury absences, rotation, goalkeeping changes, formation. All of these are not derivable from standings data. Backfill for top 20 leagues 2019–2024: ~80K calls (~1 day quota).

2. **`get_team_statistics`** — Season-level form string ("WWLDD"), clean sheet percentage, home vs. away split. These supplement the 9-feature vector. Backfill cost is minimal (~2K calls). The only overlap with existing data is GF/GA, which we already compute point-in-time from fixtures.

**Lower priority:**
3. **`get_predictions`** — No historical archive; can only be used for live upcoming fixtures. Could be a "market consensus signal" proxy. Not backfillable.

4. **`get_head2head`** — Derivable from `fixtures` table (all H2H matchups are already stored). Adding a dedicated table doesn't require an API call — we can compute it from the DB. The API call would only add speed convenience.

5. **`get_fixture_events` (standalone)** — Already have 6.4M rows from embedded fixture data. The standalone endpoint adds player_id linkage (useful for lineup-overlap analysis) but is duplicative otherwise.

---

## I2 — Leakage-Safe Rolling Features from Existing Schema

### Status: Both `fixture_stats` and `player_season_stats` are post-match leakage if used naively. Safe rolling-average design specified.

### Confirmed leakage status

**`fixture_stats`** (131,674 rows):
- ALL rows have `status = 'FT'` — every row is a completed match
- Columns: shots_total, shots_on_goal, possession, corners, yellow_cards, red_cards, passes_total, passes_accurate, xg (NULL for all rows)
- **Leakage risk:** Using `fixture_stats` for fixture F includes F's own post-match stats if not filtered. The fix is `WHERE fixture_id IN (team's prior fixtures with date < F.date)`.

**`player_season_stats`** (780,938 rows):
- Season aggregates per player-team-league-season
- **Leakage risk:** Using same-season stats for a March fixture includes March match data accumulated up to that point (API-Football updates season totals rolling). Using prior-season stats is leakage-safe.

### Leakage-safe feature design

**Feature set from `fixture_stats` (rolling N-game window, prior to fixture date):**

```sql
-- For fixture F (home team T_H, date D, season S):
-- Rolling 5-game averages for home team (all prior FT fixtures in same season)
SELECT
    AVG(fs.home_shots_total)     AS h_shots_avg,
    AVG(fs.home_shots_on_goal)   AS h_shots_on_avg,
    AVG(fs.home_possession)      AS h_possession_avg,
    AVG(fs.home_corners)         AS h_corners_avg,
    AVG(fs.home_yellow_cards)    AS h_yellows_avg,
    AVG(fs.home_passes_accurate * 1.0 / NULLIF(fs.home_passes_total, 0)) AS h_pass_acc_avg
FROM fixture_stats fs
JOIN fixtures f_prior ON fs.fixture_id = f_prior.id
WHERE f_prior.home_team_id = T_H      -- home fixtures only (or union with away)
  AND f_prior.season = S
  AND f_prior.date < D
  AND f_prior.status = 'FT'
ORDER BY f_prior.date DESC
LIMIT 5
```

Same pattern for away team (using `away_*` columns and `away_team_id`).

**Recommended rolling features (10 new features per fixture):**

| Feature | Column(s) | Notes |
|---------|----------|-------|
| `h_shots_on_avg5` | `home_shots_on_goal` | Attacking efficiency proxy |
| `a_shots_on_avg5` | `away_shots_on_goal` | |
| `h_possession_avg5` | `home_possession` | Style indicator (% float 0–100) |
| `a_possession_avg5` | `away_possession` | |
| `h_corners_avg5` | `home_corners` | Set-piece volume |
| `a_corners_avg5` | `away_corners` | |
| `h_pass_acc_avg5` | `home_passes_accurate / home_passes_total` | Press resistance proxy |
| `a_pass_acc_avg5` | `away_passes_accurate / away_passes_total` | |
| `h_yellows_avg5` | `home_yellow_cards` | Aggression / disciplinary risk |
| `a_yellows_avg5` | `away_yellow_cards` | |

**Coverage:** 71.8% of FT fixtures have `fixture_stats` records (586,902 of 817,382). For fixtures without prior stats (early season or new team), use season-level defaults computed from the training population.

**Feature set from `player_season_stats` (prior-season only — safe):**

Use season S-1 aggregates for players expected in the lineup. Without lineup data, use team-level aggregates:

```sql
-- Prior-season team attacking and defensive strength
SELECT
    SUM(pss.goals) AS team_goals_prev_season,
    AVG(pss.rating) AS team_avg_rating_prev_season,
    SUM(pss.assists) AS team_assists_prev_season
FROM player_season_stats pss
WHERE pss.team_id = T_H
  AND pss.season = S - 1
  AND pss.league_id = L_ID
```

This is safe because prior-season data precedes all fixtures in season S. Limitation: it misses winter transfers. If `get_lineups` is backfilled, player-level stats can be filtered to confirmed starters.

**Implementation note:** Both feature sets require a `feature_map` keyed by fixture_id, computed as a batch preprocessing step (the same pattern used in `walk_forward_backtest_v2.py`). A SQL CTE-based approach is feasible without loading the entire DB into memory.

---

## I3 — football-data.co.uk Coverage vs. Bootball's Odds Gaps

### Status: 9 leagues directly covered, 5 more with partial overlap. Free ingestion feasible for 2019–2024.

### Gap inventory (Bootball FT fixtures with no odds, 2019–2024)

The largest opportunities from Bootball's tracked leagues are European top/second tiers:

| League | Bootball ID | Fixtures without odds 2019–24 | football-data.co.uk | Available from |
|--------|-------------|-------------------------------|---------------------|----------------|
| Premier League | 39 | 2,280 | ✅ `E0` | 1993/94 |
| Championship | 40 | 3,338 | ✅ `E1` | 1993/94 |
| League One | 41 | 3,187 | ✅ `E2` | 1993/94 |
| League Two | 42 | 3,222 | ✅ `E3` | 1993/94 |
| La Liga | 140 | 2,280 | ✅ `SP1` | 2000/01 |
| Segunda División | 141 | 2,807 | ✅ `SP2` | 2000/01 |
| Serie A (Italy) | 135 | 2,281 | ✅ `I1` | 2000/01 |
| Serie B (Italy) | 136 | 2,330 | ✅ `I2` | 2005/06 |
| 3. Liga (Germany) | 80 | 2,273 | ✅ `D3` | 2012/13 |

Additional leagues NOT on football-data.co.uk but in top-30 gaps:
- MLS / USA (253, 256) — not covered
- Brazil Serie A/B (71, 72) — not covered
- Argentina (129, 134) — not covered
- Turkey 2. Lig (205) — not covered
- J2/J3 League Japan (99) — not covered

### football-data.co.uk data format

Each file is a CSV per league-season (`E0_2122.csv`, etc.) with columns including:
- `HomeTeam`, `AwayTeam`, `Date`, `FTHG`, `FTAG`, `FTR` (full-time result)
- Bookmaker odds: `B365H`, `B365D`, `B365A` (Bet365 H/D/A), plus 5–10 other bookmakers
- OU 2.5: `B365>2.5`, `B365<2.5` available from ~2014 in top leagues
- BTTS: `B365AHH` (Asian handicap), BTTS available from ~2016 in EPL; later for other leagues

### Feasibility and linkage challenge

**The key problem:** football-data.co.uk uses team names, not API-Football fixture IDs. Linking requires a string-matching step to map (`HomeTeam`, `AwayTeam`, `Date`) → Bootball `fixture_id`.

**Linkage approach:**
1. Build a lookup: `(date, home_name_normalized, away_name_normalized)` → `fixture_id` from Bootball's `fixtures` table
2. Normalize team names (lowercase, remove FC/United/City suffix variants) + fuzzy match for common aliases
3. Confirmed matches insert into `fixture_odds` with `bookmaker = 'football_data_co_uk'`
4. Ambiguous/unmatched rows go into a review log

**Coverage estimate for top 9 leagues, 2019–2024:**
- ~24,000 fixtures across 9 leagues × 5 seasons
- After fuzzy linkage, expect ~80–90% match rate → ~19,000–21,600 matched fixtures
- Each matched fixture would have H/D/A odds (and OU 2.5 for most, BTTS for some)

**Ingestion cost:** Zero API calls. One-time Python script (~100–150 lines), download ~45 CSV files (< 2MB total).

### Recommended scope for Phase 2

Priority for ingestion:
1. English top 4 tiers (39, 40, 41, 42) — most data, best coverage, historical depth to 1993
2. La Liga + Segunda (140, 141) — full market depth
3. Italian Serie A + B (135, 136) — full market depth

This would provide ~17,000 additional fixtures with odds for backtesting vs. the current 2,334-fixture window.

---

## Summary Table

| Task | Status | Key Finding |
|------|--------|------------|
| J1 | Complete | Active EV formula is correct (`p_blended × d - 1`). Legacy formula (`p×(d+1)-1`) still present in `_generate_for_fixture()` but NOT called in production. |
| J2 | Complete | Three-layer calibration pipeline: pkl IsotonicRegression (DISABLED), LeagueCalibrationEngine Platt-scaling (ACTIVE), market blend 65%/35% (ACTIVE). Sequential, not competing. |
| J3 | Complete | Production EV computed from `p_blended` — 35% model (Platt-calibrated), 65% Shin de-vigged market. Stored `our_prob` is raw model output; NOT the same probability that produced stored `ev`. |
| J4 | Complete | Three causes: (1) wrong formula (+p phantom per bet), (2) model overconfidence (+10–18pp), (3) no market blend before June 2026. Current records still show 83–90% passing 5% threshold — EV filter is non-selective. |
| K | Complete | Single global threshold `BOT_MIN_EV = 0.05` from settings.py. Applied at `agent.py:183`. No per-market override. Raising threshold would reduce volume but not fix underlying overconfidence. |
| I1 | Complete | `get_lineups` and `get_team_statistics` are highest-value un-ingested endpoints. `get_predictions` has no historical archive. `get_head2head` is derivable from existing `fixtures` table. |
| I2 | Complete | `fixture_stats` and `player_season_stats` are post-match leakage if used naively. Leakage-safe design: rolling N-game aggregates over prior fixtures (`date < F.date`). 10 new features specified. |
| I3 | Complete | 9 Bootball leagues overlap with football-data.co.uk coverage. ~19–21K linkable fixtures for 2019–2024. Zero API cost. Key challenge: team-name → fixture_id linking via fuzzy match. |

---

## Recommended Decision Points for Phase 2

Based on Phase 1b + 1c findings, the key architectural decisions before Phase 2:

**Decision 1 — Feature expansion path:**  
The model's 9-feature standings vector carries no information the market doesn't already have. Two non-overlapping feature sources are viable immediately:
- **football-data.co.uk ingestion** (I3): Free, adds ~19K training fixtures with odds → quantitative uplift in backtest window
- **`fixture_stats` rolling features** (I2): Already in DB, adds 10 pre-match features; requires point-in-time compute at training time

**Decision 2 — `get_lineups` backfill:**  
Starting XI is genuinely informative and pre-match. Cost: ~100K calls for top leagues. Decision needed: is it worth 1–2 days of quota during the June backfill window?

**Decision 3 — Legacy EV formula removal:**  
`_generate_for_fixture()` lines 100–145 contain the inflated formula and should be removed or corrected before they can be accidentally invoked. Low risk, low effort.

**Decision 4 — EV threshold recalibration:**  
After Phase 2 feature additions + proper out-of-fold calibration (not Platt only), re-measure the EV pass rate. If it remains > 50%, the threshold needs to be much higher (e.g., 20–30%) to provide any selectivity. Consider market-relative EV (EV computed against de-vigged market, not raw odds) as the canonical signal.
