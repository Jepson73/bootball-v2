# Phase 36 — Player/Lineup Data Scoping (Track A resolution lever)

Scoping only. Nothing built, nothing retrained. Bounded probe: 78 live API calls used (projected ≤100), against ~41k calls remaining today at probe time (33,990/75,000 used). No backfill, no writes beyond a throwaway probe script + its JSON output.

## 1. In-DB player-data inventory (zero API calls)

| Table | Rows | What it is | Coverage window |
|---|---|---|---|
| `player_season_stats` | 780,938 | The "780k unused rows" from the original audit. Per player/team/season/league: appearances, lineups(count), minutes, rating, goals, assists, goals_conceded, saves, shots, passes, tackles, duels, dribbles, cards, fouls, penalties, position, photo. | 875 distinct leagues, seasons **2021–2025 only**. Entire table was written in a single 5-day burst, 2026-06-04 → 2026-06-08 (matches the known bulk-backfill window). |
| `players` | 49,053 | Simpler current-squad snapshot: name/position/goals/assists/cards/minutes, no season dimension. | 3,545 teams, fetched 2026-04-28 → 2026-05-27. Predates and is superseded by `player_season_stats`; likely dead weight now. |
| `injuries` | 586 | player/team/type/status/start-end dates, keyed to fixture_id. | **All 586 rows fetched in one 2-minute window on 2026-04-12.** Only 8 leagues represented: England PL, MLS, Eredivisie, Bundesliga, La Liga, Serie A, Ligue 1, A-League (Australia). Read as a one-off proof-of-concept fetch, not a running collection. |
| `player_fetch_log` | 66,999 | Bookkeeping for the June backfill (team × season fetch receipts). | 22,521 distinct team-seasons, 2021–2025. |
| lineups / sidelined / squads / coaches | **none** | No tables exist. `client.get_lineups()` is implemented in `src/ingestion/client.py:290` but is called from nowhere in the codebase and has no ORM model or table to land in. `fixture_events` (6.44M rows) is in-match goal/card/sub timeline, not lineups. `match_events` is an empty, apparently-dead table. | — |

**Decisive join — does the settled prediction pool actually have player data to train on?**

Joining `prediction_records WHERE settled=1` (44,541 rows) to `player_season_stats` on exact (team_id, season, league_id) for both home and away:

| Fixture season | Settled predictions | Both-team player coverage |
|---|---|---|
| 2025 | 30,543 (68.6%) | 7,223 (23.6%) |
| 2026 | 13,998 (31.4%) | **0 (0%)** |
| **Total** | **44,541** | **7,223 (16.2%)** |

The 2026 gap isn't a data-quality problem, it's a scheduling one: the June backfill stopped at season 2025 and nothing has kept the current season topped up since. Every settled prediction from the live 2026 season — a third of the whole pool — has zero player rows to join against, by construction. Even within the backfilled 2025 season, only ~1 in 4 settled fixtures has complete data for both teams — the 875-league backfill was broad but shallow, and (per Task 2 below) shallowest exactly where the model spends most of its time.

Field completeness within `player_season_stats`: position present 99.9%, minutes>0 54.4%, rating present only 13.7% (API-Football's proprietary post-match rating is sparse outside well-covered leagues — expect this to track the same tier split as Task 2).

## 2. Provider coverage probe — the go/no-go

Sampled 36 recent FT fixtures across 9 leagues / 4 tiers (4 fixtures each), probed `fixtures/lineups` + `injuries` (by fixture) for each. 78 calls used. All zero-result responses came back as clean empty payloads (`errors: []`, `paging.total: 1`) — genuine provider gaps, not plan/access errors.

| Tier | League | Confirmed lineups | Injuries |
|---|---|---|---|
| Top-flight | England Premier League | 4/4, full XI+coach+formation | 12–14 records/fixture |
| Top-flight | Spain La Liga | 4/4, full XI+coach+formation | 14–24 records/fixture |
| MLS/USL | MLS | 4/4, full XI+coach+formation | 12–16 records/fixture |
| MLS/USL | USL League Two | **0/4** | **0/4** |
| Serie D-class | Brazil Serie D | **0/4** | **0/4** |
| Serie D-class | Italy Serie D — Girone A | **0/4** | **0/4** |
| Long tail | Ethiopia Premier League | **0/4** | **0/4** |
| Long tail | Azadegan League (Iran) | **0/4** | **0/4** |
| Long tail | GFA League (Gambia) | **0/4** | **0/4** |
| Long tail | Ligi kuu Bara (Tanzania) | 4/4, full XI, coach flaky | **0/4** |

**Verdict: coverage is inverted relative to the model's actual habitat, and it's not subtle.** Cross-referencing against where our settled predictions actually come from (Brazil Serie D, USL League Two, MLS Next Pro, Ethiopia PL, Azadegan Iran, Tanzania, Gambia, Benin, Kenya, etc. dominate the settled-prediction volume — see the habitat table derived from `prediction_records` × `fixtures` × `leagues`), the leagues with rich lineup/injury data are almost exactly the small, already-efficient slice of volume the earlier audit flagged as offering little exploitable edge. The leagues carrying Track A's actual resolution problem (btts/ou25 as calibrated base-rate machines) are the ones the provider has nothing for. MLS/USL split within a single nominal "tier" (MLS: full data; USL League Two: nothing) shows the boundary is about provider prestige, not divisional level — it won't line up neatly with any tier field we already store.

Caveat: 9 leagues is a thin sample of an 875-league footprint. It's enough to establish the inversion is real and large, not enough to enumerate the full "covered" set — that enumeration is exactly what a wider (still cheap) probe would need to do before a Phase 37 build.

## 3. Cost model

**Forward (daily collection for covered leagues):** Current NS-fixture volume in-DB runs roughly 150–200/day (spot-checked over the next week: 47–662 depending on day-of-week clustering). The data-rich subset (top-flight + MLS-tier, per Task 2) is a minority of that. Injuries can be fetched once per league per day (`league` + `date` params) rather than per-fixture — for a generous ~20-league covered set that's ~20 calls/day. Confirmed lineups still cost 1 call/fixture near kickoff and only make sense for the same covered subset — order of 30–60 calls/day at current volume. **Total forward cost: roughly 50–100 calls/day** — noise against a 75k/day budget and the ~41k remaining headroom observed at probe time. Forward collection is not the constraint.

**Backfill (training-set assembly, covered leagues only):** Two components:
- Close the 2026-season gap in `player_season_stats` for leagues that already have 2021–2025 rows — this is the existing `/players` bulk endpoint (already coded in `backfill.py`), just needs to run forward. Order of a few hundred to ~1,000 calls given the league count involved.
- Historical `fixtures/lineups` + `injuries` for the *covered-league subset* of the 44,541 settled predictions — no batch endpoint exists for lineups (1 call/fixture), so cost scales directly with however many covered-league settled fixtures exist. Because coverage is concentrated in a handful of leagues that are a minority of settled volume, this is very likely a few thousand calls, not tens of thousands — comfortably inside an elastic backfill budget, completable in days at a conservative 500–1,000 calls/day pace.

**Bottom line: neither forward nor backfill cost is prohibitive. Coverage — not budget — is the binding constraint on this lever.**

## 4. Feature & leakage design (paper only)

**Availability tier (days out — feeds the standing prediction):**
- Absence-adjusted team strength: share of the team's recent-season goals/minutes belonging to players currently flagged injured/suspended (from `injuries`/sidelined once ingested), computed strictly from data timestamped before the prediction freeze.
- Keeper availability flag (specific position weight — keeper absence is a distinct signal from an outfield absence of similar "minutes share").
- New-coach flag (coach change within N days, from lineup coach field history once collected) as a regime-shift proxy.
- Leakage rule: any availability feature must be built only from injury/sidelined/squad records whose own timestamp precedes the fixture's prediction-freeze time — no using data that was only knowable after freeze, and no using post-fixture-confirmed lineups to backfill pre-fixture "expected XI."

**Confirmed-XI tier (kickoff-minus-~1h — powers a near-kickoff prediction UPDATE only, not the original prediction):**
- Rotation intensity: overlap between the confirmed XI and each team's modal recent-XI (e.g., Jaccard similarity over last-5 starting lineups).
- Confirmed absences vs. expected: difference between the availability-tier's absence assumption and what the confirmed lineup actually shows (catches late scratches/rotations the availability tier couldn't see).
- This activates the deferred "player-news → re-predict" branch: **supersede-and-retain** (per the Phase 20 taxonomy — the original prediction stays in the record, a new one supersedes it), new `served_prob` frozen at the update moment per the Phase 33b Rider 4 convention (freeze served_prob at kickoff, no retroactive rewrites of settlement display).
- **Track A must segment by prediction lead-time** once this exists: a day-out prediction and a kickoff-minus-1h update for the same fixture are not comparable resolution samples and must never be pooled in the same scoring bucket — otherwise the near-kickoff tier's presumably-better resolution would contaminate (or be contaminated by) the day-out tier's numbers.

**Target metric:** btts/ou25 resolution (bin spread + Brier on a chronological holdout) is primary; h2h is secondary; ou15 must not degrade.

## 5. Recommendation + pre-registered bar

**Recommendation: narrow, conditional go — not a blanket one.**

The lever is real but small. Player/lineup/injury data exists at the provider only for a data-rich minority of leagues (top-flight Europe + MLS, from this sample) that is disproportionately *not* where the model's settled-prediction volume lives, and disproportionately *is* the kind of efficient market the earlier (betting-bar) audit already found offered little edge. For the long-tail/Serie-D-class/USL2 leagues carrying Track A's actual btts/ou25 resolution problem, this lever is currently a **no-go**: the provider has nothing to sell, and no internal engineering closes that gap. Cost (Task 3) is not what's holding this back — coverage is.

If pursued, scope Phase 37 to the covered-league subset only, and treat it explicitly as a partial fix, not a general resolution to Track A's btts/ou25 verdict.

**Pre-registered success bar (written now, before any model exists):**

> Player/availability features justify a production build (covered leagues only) if, on a chronological holdout:
> - btts or ou25 Brier improves by ≥0.005, **with calibrated bin spread demonstrably widening** (resolution improving, not just reliability shifting),
> - ou15 stays within 0.002 of baseline Brier (must-not-degrade),
> - the improvement holds across **at least 2 non-overlapping chronological holdout folds** — not just one.

The extra fold requirement is new relative to a generic bar, and it's not optional: the exact-match join above shows on the order of a few thousand usable historical rows even before restricting to covered leagues, and restricting to covered leagues shrinks that further. A single-fold result on a training set this size is not trustworthy enough to greenlight a build; two independent folds agreeing is the minimum bar this data volume can actually support.

## Artifacts
- `scripts/analysis/phase36_provider_probe.py` — the probe script (rerunning costs 0 calls; cache-aware client returns cached responses for the same 36 fixtures).
- `scripts/analysis/phase36_probe_results.json` — raw probe output.
