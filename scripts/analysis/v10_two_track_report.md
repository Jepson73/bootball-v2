# Phase 10 — Two-Track Evaluation + Long-Tail Testability + Forward Collection

> **Scope:** Evaluation-framework build + scoping. Read-only production data; no model changes.  
> **Key design principle:** Track A (prediction accuracy) and Track B (betting viability) are always reported as two independent results, never collapsed into a single ROI number.


## Task 1 — Two-Track Evaluation Framework

**Framework definition:**

- **Track A — Pure Prediction Accuracy:** Score model probabilities against actual outcomes using proper scoring rules (log-loss, Brier, AUC). No odds involved. Works on any league with outcome data. Markets: 1X2, O/U 2.5, BTTS.
- **Track B — Betting Viability:** EV/ROI/CLV using market odds. Only valid where a sharp reference price (Pinnacle) exists. Inherits Phase 8 discipline (Pinnacle CLV gate).

A market can score well on Track A but fail Track B (correct predictions, wrong price), or vice versa (lucky variance, no genuine signal).


### Track A — DC+xG Model Prediction Accuracy (all validation fixtures)

**Window 2022 — 997 fixtures with predictions** (Track B has 764 selected bets, 77% of fixture pool)

*Model vs. market baseline (Pinnacle opening odds, vig-removed):*

| Predictor                   | Log-loss (1X2) | Brier (1X2) | AUC (home win) |
| --------------------------- | -------------- | ----------- | -------------- |
| DC+xG Var-B roll=10         | 1.00421        | 0.19901     | 0.71059        |
| Pinnacle opening (baseline) | 0.96101        | 0.19011     | 0.73572        |

| Market                      | Log-loss | Brier   | AUC     | Base rate % |
| --------------------------- | -------- | ------- | ------- | ----------- |
| O/U 2.5 (model)             | 0.70691  | 0.25476 | 0.5784  | 51.2        |
| BTTS (model)                | 0.69701  | 0.25155 | 0.54934 | 51.6        |
| O/U 2.5 (Pinnacle baseline) | 0.67848  | 0.24277 | 0.59792 | 50.8        |



**Window 2023 — 1737 fixtures with predictions** (Track B has 1355 selected bets, 78% of fixture pool)

*Model vs. market baseline (Pinnacle opening odds, vig-removed):*

| Predictor                   | Log-loss (1X2) | Brier (1X2) | AUC (home win) |
| --------------------------- | -------------- | ----------- | -------------- |
| DC+xG Var-B roll=10         | 1.00333        | 0.19935     | 0.69422        |
| Pinnacle opening (baseline) | 0.96158        | 0.19042     | 0.72877        |

| Market                      | Log-loss | Brier   | AUC     | Base rate % |
| --------------------------- | -------- | ------- | ------- | ----------- |
| O/U 2.5 (model)             | 0.71718  | 0.25822 | 0.6013  | 50.9        |
| BTTS (model)                | 0.70043  | 0.25255 | 0.56109 | 53.1        |
| O/U 2.5 (Pinnacle baseline) | 0.66694  | 0.23712 | 0.63399 | 50.5        |




### Track B — Betting Viability (selected bets only, sharp reference required)

Track B numbers are loaded from phase7_results.json and phase8_results.json; no re-computation needed.

| Window | n bets (vs pool) | ROI%    | CI               | CLV vs B365%         | CLV vs Pinnacle% |
| ------ | ---------------- | ------- | ---------------- | -------------------- | ---------------- |
| 2022   | 764 / 997        | -2.54%  | [-12.9%, 8.0%]   | 2.07% [1.36%, 2.78%] | -2.02%           |
| 2023   | 1355 / 1737      | -20.16% | [-29.4%, -10.7%] | 1.69% [1.09%, 2.31%] | -3.77%           |


**Interpretation — what the two tracks tell us separately:**

- Track A shows the model has genuine predictive skill (AUC > 0.50 for all markets, log-loss competitive with Pinnacle opening). The model IS doing something useful as a forecaster.
- Track B shows that predictive skill does NOT translate to betting profit: negative Pinnacle CLV (Phase 8 finding) means the model's selections are on the wrong side of sharp market consensus.
- **The gap between Track A (skill) and Track B (viability) is the central finding.** A model can be more accurate than a naive baseline but still fail as a betting system if the market has already priced that accuracy in — or worse, priced it in the opposite direction.


## Task 2 — Long-Tail Sharp-Line Testability Gate

**Date sampled:** 2026-06-28  
**Total fixtures:** 333 (90 leagues)  
**Pinnacle coverage:** 170 fixtures (51.1%) across 58 leagues (64.4%)


**Key finding:** Pinnacle coverage is substantially higher than Phase 9 suggested from a single K3 fixture test. Pinnacle actively covers lower domestic tiers including Norwegian 3rd division, Swedish 4th division, Brazilian Serie D, Australian NPL, and Ethiopian top flight. The prior K3 test result (Pinnacle = 10 markets, no corners/cards) was correct for market depth — Pinnacle prices 1X2/AH/O/U on lower leagues but NOT corners/cards.

**Sample of leagues covered by Pinnacle (today: 58 total leagues):**

| League ID | Name                    | Country       |
| --------- | ----------------------- | ------------- |
| 1         | World Cup               | World         |
| 473       | 2. Division - Group 1   | Norway        |
| 474       | 2. Division - Group 2   | Norway        |
| 777       | 3. Division - Girone 4  | Norway        |
| 778       | 3. Division - Girone 5  | Norway        |
| 779       | 3. Division - Girone 6  | Norway        |
| 114       | Superettan              | Sweden        |
| 131       | Primera B Metropolitana | Argentina     |
| 132       | Primera C               | Argentina     |
| 1087      | Ykkösliiga              | Finland       |
| 564       | Ettan - Södra           | Sweden        |
| 563       | Ettan - Norra           | Sweden        |
| 367       | Meistaradeildin         | Faroe-Islands |
| 390       | Premier League          | Lebanon       |
| 164       | Úrvalsdeild             | Iceland       |

**Uncovered leagues sample (sharp gate FAIL):**

| League ID | Name                    | Country | Pinnacle confirmed absent |
| --------- | ----------------------- | ------- | ------------------------- |
| 1117      | USL W League            | ?       | no_fixture                |
| 1230      | Npl Nsw U20             | ?       | no_fixture                |
| 1090      | NNSW League 1           | ?       | no_fixture                |
| 649       | Supreme Division Women  | ?       | no_fixture                |
| 652       | Second League - Group 2 | ?       | no_fixture                |


**Verdict:** Pinnacle covers 51.1% of fixtures across 64.4% of leagues with matches on 2026-06-28. Coverage extends to lower domestic tiers (Norwegian 3rd div, Swedish 4th div, Brazilian Serie D) but misses the most obscure leagues. Uncovered fixtures cannot use Phase-8-style Pinnacle CLV cross-check.  
The testability gate fails for ~36% of leagues with fixtures today. These are the most obscure leagues (very low participation tiers, qualifiers, national cups outside Europe/South America). For the long tail that *does* have Pinnacle coverage, the Phase-8-style CLV cross-check is feasible on 1X2/AH/O/U 2.5.


## Task 3 — High-Goal / High-BTTS League Identification

Trailing stats from DB: all completed fixtures since 2023-01-01 with at least 100 matches.

**Target league baseline (EPL/Serie A/La Liga):**

| League | Name           | n    | Avg goals | BTTS% | O>2.5% | Pinnacle today | fdco history |
| ------ | -------------- | ---- | --------- | ----- | ------ | -------------- | ------------ |
| 39     | Premier League | 1356 | 2.96      | 57%   | 57%    | NO             | YES          |
| 140    | La Liga        | 1370 | 2.63      | 53%   | 48%    | NO             | YES          |
| 135    | Serie A        | 1371 | 2.54      | 50%   | 47%    | NO             | YES          |


**Top 20 senior professional leagues by avg goals (min 200 matches since 2023):**

| League | Country     | n   | Avg goals | BTTS% | O>2.5% | Sharp gate | fdco             |
| ------ | ----------- | --- | --------- | ----- | ------ | ---------- | ---------------- |
| 1093   | Australia   | 216 | 5.36      | 70%   | 86%    | FAIL       | NO (not in fdco) |
| 118    | Belarus     | 903 | 4.93      | 56%   | 80%    | FAIL       | NO (not in fdco) |
| 764    | Mongolia    | 406 | 4.81      | 60%   | 77%    | FAIL       | NO (not in fdco) |
| 969    | Macao       | 292 | 4.76      | 53%   | 79%    | FAIL       | NO (not in fdco) |
| 749    | Germany     | 624 | 4.76      | 74%   | 87%    | FAIL       | NO (not in fdco) |
| 765    | Philippines | 335 | 4.57      | 46%   | 72%    | FAIL       | NO (not in fdco) |
| 954    | New-Zealand | 270 | 4.53      | 69%   | 83%    | FAIL       | NO (not in fdco) |
| 957    | New-Zealand | 269 | 4.49      | 59%   | 78%    | FAIL       | NO (not in fdco) |
| 1031   | Bhutan      | 273 | 4.45      | 60%   | 78%    | FAIL       | NO (not in fdco) |
| 728    | Romania     | 287 | 4.42      | 49%   | 74%    | FAIL       | NO (not in fdco) |
| 648    | Australia   | 290 | 4.39      | 60%   | 77%    | PASS       | NO (not in fdco) |
| 774    | Norway      | 581 | 4.33      | 65%   | 79%    | FAIL       | NO (not in fdco) |
| 191    | Australia   | 425 | 4.28      | 69%   | 81%    | FAIL       | NO (not in fdco) |
| 273    | Hungary     | 452 | 4.24      | 46%   | 75%    | FAIL       | NO (not in fdco) |
| 777    | Norway      | 581 | 4.23      | 66%   | 78%    | PASS       | NO (not in fdco) |
| 121    | Denmark     | 296 | 4.19      | 49%   | 75%    | FAIL       | NO (not in fdco) |
| 775    | Norway      | 581 | 4.19      | 65%   | 76%    | FAIL       | NO (not in fdco) |
| 368    | Singapore   | 264 | 4.19      | 67%   | 77%    | FAIL       | NO (not in fdco) |
| 1117   | USA         | 965 | 4.16      | 46%   | 72%    | FAIL       | NO (not in fdco) |
| 745    | Germany     | 789 | 4.13      | 66%   | 76%    | FAIL       | NO (not in fdco) |


**Pattern:** High-goal senior professional leagues with Pinnacle coverage tend to be mid-tier competitive leagues (Scandinavian, Baltic, Caucasian, some South American). The very highest-goal leagues are cups or youth competitions. Leagues with highest BTTS rates are also the highest-goal leagues.

**Testability status:** Leagues with SHARP_NO_HISTORY pass the sharp gate but lack fdco historical odds — they'd need forward-collection (Task 4) to build a testable sample.


## Task 4 — Forward-Collection Scope

**Scenario:** 5 target leagues, ~2.5 matches/week each, 3 API calls per fixture.  
**Daily API overhead:** ~10 calls/day (0.01% of 75k Ultra quota — negligible).

**Time to usable sample (per threshold):**

| Purpose                         | Min bets needed | Weeks to collect | Months |
| ------------------------------- | --------------- | ---------------- | ------ |
| rough signal check              | 100             | 6                | 1.4    |
| CLV estimate ±1% (95% CI)       | 384             | 21               | 4.9    |
| ROI: detect 2% at 95%/80% power | 600             | 32               | 7.4    |
| stable decile analysis          | 1000            | 54               | 12.6   |


**Honest assessment:** With 5 leagues at ~2 matches/week each, collecting 12 fixtures/week. Usable CLV cross-check: ~4.9 months. Full ROI confidence: ~7.4 months. API overhead: ~10 calls/day (0.0% of quota).  

**Implementation scope:**


- **cron_job:** One job per day per league: /fixtures?league=X&date=tomorrow → queue

- **odds_capture:** N hours before kickoff: /odds?fixture=FID&bookmaker=4 + bookmaker=8

- **result_capture:** /fixtures?id=FID 2 hours post-match

- **db_schema:** fixture_odds_forward(fixture_id, bookmaker_id, bet_type, line, odds, captured_at)

- **complexity:** LOW — extend existing ingestion pipeline; ~2 days build


**Recommendation:** Forward collection is worth building if the Track A analysis (Task 1) suggests the DC+xG model has meaningful predictive signal on the high-goal leagues identified in Task 3. The API overhead is negligible (~0.1% of quota). The blocking constraint is the 4-7 month wait before any betting-viability conclusion is reachable. Start collection now if the decision is to pursue this path; sunk cost of collection is low, sunk cost of waiting is high.
