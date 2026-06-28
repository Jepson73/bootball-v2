# Phase 9 — Alternative-Market Scoping

> **Scope:** Gate check before any modeling. Three conjunctive gates: (1) sharp reference price, (2) historical odds backfill, (3) outcome data in DB.  
> **Ground rules:** Read-only DB; live API calls (rate-limited); web research for backfill.  
> **Constraint from Phase 8:** Any market must permit a Pinnacle CLV cross-check at the *front*, not the end. Soft-book-only markets excluded regardless of backtest.


## Task 1 — API-Football Market Inventory

API-Football registry: **338 bet types**, **33 bookmakers**.  
Pinnacle (id=4) and Betfair (id=3) both registered.

**Sample fixture coverage** — two tiers tested:

- `fixture=1520517` K3 League (domestic, lower tier): Dangjin vs Gangneung

  - Pinnacle: 10 markets; Betfair: 10 markets

  - Pinnacle 1X2 margin: 7.85%; Betfair 1X2 margin: 11.97%

  - Pinnacle corner markets: NONE

  - Pinnacle card markets: NONE

- `fixture=1561329` World Cup 2026: South Africa vs Canada

  - Pinnacle: 20 markets; Betfair: 17 markets

  - Pinnacle 1X2 margin: 3.02%; Betfair 1X2 margin: 5.06%

  - Pinnacle corner markets: ['Corners 1x2', 'Corners Over Under', 'Corners Asian Handicap', 'Home Corners Over/Under', 'Away Corners Over/Under', 'Total Corners (1st Half)']

  - Pinnacle card markets: ['Cards Over/Under', 'Cards Asian Handicap']


**Key finding:** Betfair 1X2 margin is ~12% — confirms this is the **soft sportsbook**, not the Exchange. Betfair Exchange (the sharp reference) requires a separate API with a funded account and does not appear on API-Football.


## Task 2 — Sharp-Reference Gate

Pinnacle margin (World Cup 1X2): **3.02%** — genuine sharp book.  
Betfair margin (K3 1X2): **11.97%** — soft sportsbook, not usable as sharp reference.

PASS/FAIL per market:

| Market                           | Pinnacle Domestic | Pinnacle Intl | Gate 1 | Note                                                                    |
| -------------------------------- | ----------------- | ------------- | ------ | ----------------------------------------------------------------------- |
| 1X2 (Match Winner)               | ✓                 | ✓             | PASS   | Pinnacle prices on all tested tiers                                     |
| Asian Handicap                   | ✓                 | ✓             | PASS   | Pinnacle prices on all tested tiers                                     |
| Goals O/U (all lines)            | ✓                 | ✓             | PASS   | Pinnacle prices on all tested tiers                                     |
| Goals O/U 1st Half               | ✓                 | ✓             | PASS   | Pinnacle prices on all tested tiers                                     |
| Corners O/U                      | ✓*                | ✓             | FAIL*  | Pinnacle only on World Cup, not K3. EPL unconfirmed (off-season).       |
| Corners AH                       | ✓*                | ✓             | FAIL*  | Same as Corners O/U                                                     |
| Cards O/U                        | ✓*                | ✓             | FAIL*  | Pinnacle only on World Cup, not K3. Soft-book segment.                  |
| Cards AH                         | ✓*                | ✓             | FAIL*  | Same as Cards O/U                                                       |
| Both Teams Score                 | ✗                 | ✗             | FAIL   | Not priced by Pinnacle (soft-book exotic)                               |
| Double Chance                    | ✗                 | ✗             | FAIL   | Not priced by Pinnacle                                                  |
| HT/FT Double                     | ✗                 | ✗             | FAIL   | Not priced by Pinnacle on either tier                                   |
| Player markets (scorer, assists) | ✗                 | ✗             | FAIL   | Pinnacle does not price player markets                                  |
| Betfair (any market)             | ✗                 | ✗             | FAIL   | API-Football 'Betfair' is the Sportsbook, not Exchange. Margin=11.97% … |


*Asterisk: Pinnacle confirmed on international competition (World Cup) but NOT on domestic lower-tier (K3). EPL/Serie A/La Liga status unconfirmed (off-season; API-Football doesn't retain historical pre-match odds). Gate 2 failure makes EPL corner confirmation moot anyway.


## Task 3 — Historical Odds Backfill

**fdco (football-data.co.uk) — primary historical source:**

| Market          | fdco Column        | Bookmaker | Seasons       | Rows (3 leagues) | Sharp CLV checkable? |
| --------------- | ------------------ | --------- | ------------- | ---------------- | -------------------- |
| 1X2 closing     | PSCH/D/A           | Pinnacle  | 5 (1920–2324) | ~5,700           | YES                  |
| O/U 2.5 closing | PC>2.5, PC<2.5     | Pinnacle  | 5 (1920–2324) | 5691             | YES                  |
| AH closing      | PCAHH, PCAHA, AHCh | Pinnacle  | 5 (1920–2324) | 5700             | YES                  |
| Corners odds    | —                  | NONE      | —             | —                | NO (not in fdco)     |
| Cards odds      | —                  | NONE      | —             | —                | NO (not in fdco)     |

**historical_odds.db:** empty (no tables)

**Other backfill sources assessed:**

- **betexplorer:** Historical odds available for some markets (scraping, terms unclear)

- **oddsportal:** Historical odds archive (scraping required, rate-limited, terms unclear)

- **football_data_co_uk:** fdco — already included above

- **betfair_exchange_historical:** Betfair provides historical exchange data via Data Catalogue (subscription required, ~£50/mo or per-file purchase)


## Task 4 — Outcome Data Availability

All outcome data is from `football.db` ingested via API-Football fixture endpoint.

| Market                      | DB Source                                           | Coverage (all leagues) | Target league 3-season coverage |
| --------------------------- | --------------------------------------------------- | ---------------------- | ------------------------------- |
| 1X2 / scoreline             | fixtures.home_goals + away_goals                    | ~100%                  | ~100%                           |
| O/U 2.5                     | same scoreline                                      | ~100%                  | ~100%                           |
| Asian Handicap              | same scoreline                                      | ~100%                  | ~100%                           |
| Total Corners               | fixture_stats.home_corners + away_corners           | 98.2%                  | ~98%+ (EPL/Serie A/La Liga)     |
| Yellow Cards (team)         | fixture_stats.home_yellow_cards + away_yellow_cards | 96.1%                  | ~96%+                           |
| Red Cards (team)            | fixture_stats.home_red_cards                        | >95%                   | >95%                            |
| Card events (player/minute) | fixture_events WHERE event_type='Card'              | 1.24M yellow, 107k red | N/A — inconsistent player match |

Target league corner coverage (3 seasons: 2021-22, 2022-23, 2023-24):

- EPL: 1140/1140 (100.0%)

- Serie A: 1140/1141 (99.9%)

- La Liga: 1140/1140 (100.0%)


## Task 5 — Candidate Shortlist

Three-gate matrix:

| Rank | Market                                     | Gate 1 Sharp | Gate 2 Backfill | Gate 3 Outcomes | Verdict        |
| ---- | ------------------------------------------ | ------------ | --------------- | --------------- | -------------- |
| 1    | Goals O/U 2.5                              | PASS         | PASS            | PASS            | PASS all gates |
| 2    | Asian Handicap                             | PASS         | PASS            | PASS            | PASS all gates |
| 3    | Corners O/U                                | FAIL*        | FAIL            | PASS            | FAIL gates 1+2 |
| 4    | Cards O/U                                  | FAIL*        | FAIL            | PASS            | FAIL gates 1+2 |
| 5    | Both Teams Score / Double Chance / HT-FT   | FAIL         | FAIL            | PASS            | FAIL gates 1+2 |
| 6    | Player markets (scorer, assists, bookings) | FAIL         | FAIL            | PARTIAL         | FAIL gates 1+2 |


### Passing markets (all 3 gates)

**1. Goals O/U 2.5**

- Gate 1: Pinnacle prices PC>2.5 / PC<2.5; confirmed K3 and World Cup

- Gate 2: fdco PC>2.5 closing: 5691 rows across 3 leagues × 5 seasons

- Gate 3: Scoreline → total goals; 100% from fixtures table

- Structural note: *Downstream of same DC+xG signal as 1X2. P(>2.5 goals) is a transformation of μ_home + μ_away from Poisson. Phase 8's negative Pinnacle CLV on 1X2 would likely transfer: Pinnacle's O/U price already subsumes its superior goal estimate. Not independent of 1X2 finding.*



**2. Asian Handicap**

- Gate 1: Pinnacle prices PCAHH / PCAHA; confirmed K3 and World Cup

- Gate 2: fdco PCAHH/PCAHA closing: 5700 rows across 3 leagues × 5 seasons

- Gate 3: Scoreline → AH winner; 100% settleable from fixtures

- Structural note: *Eliminates draw and re-prices on Pinnacle's expected goal difference. Same underlying signal (μ_home − μ_away) as DC model. More efficient than 1X2 due to tighter spread; edge harder to find, not easier.*




### Failing markets

**Corners O/U** — FAIL gates 1+2

- Gate 2: fdco has no corner betting odds (only HC/AC outcome counts). historical_odds.db empty. No free historical source identified.

- Structural note: Even if Pinnacle prices EPL corners: no historical odds to build or validate a model against the sharp line. Cannot reproduce Phase-8-style Pinnacle CLV check. Hard gate blocks this path.



**Cards O/U** — FAIL gates 1+2

- Gate 2: No historical card odds in any identified free source.

- Structural note: Even with Pinnacle coverage: no historical reference price for CLV. Cards are influenced by referee, match stakes, late substitutions — poor fit for DC+xG model family.



**Both Teams Score / Double Chance / HT-FT** — FAIL gates 1+2

- Gate 2: No Pinnacle closing available in fdco for these markets.

- Structural note: Soft-book exotics; no sharp reference available.



**Player markets (scorer, assists, bookings)** — FAIL gates 1+2

- Gate 2: No historical player market odds in free sources.

- Structural note: Player markets require player-level model; entirely out of scope.




### Overall verdict

Two markets pass all three gates: Goals O/U 2.5 and Asian Handicap. Both have Pinnacle closing prices in fdco (5 seasons × 3 leagues, ~5,700 rows each). Both are outcome-settleable from the scoreline. HOWEVER: both are downstream of the same DC+xG expected-goal signal as 1X2. Phase 8 showed negative Pinnacle CLV on 1X2 because the model tracks public/retail money. That structural problem transfers to O/U and AH — Pinnacle's AH and O/U lines already encode its superior goal estimate. No independent modeling path exists without a fundamentally different signal source (player data, line-movement, market microstructure).


**Implication for research arc:** Phase 9 finds no market that passes all three gates AND provides a signal independent of the DC+xG goal model. O/U 2.5 and AH are mechanically reachable but structurally redundant. Phase 8's STOP_ENTIRELY verdict stands. A new direction would require a different model family (player-level, line-movement, market microstructure) or a different data source (Betfair Exchange historical data, subscription-grade odds history).
