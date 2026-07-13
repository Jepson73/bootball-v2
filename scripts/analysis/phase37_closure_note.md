# Phase 37 Part B — Closure Note: Availability-Tier Features FAIL the Pre-Registered Bar

**Verdict: FAIL.** Per Phase 36's pre-registered bar, this is a valid, final answer for the player-availability lever at this provider, for this scope (the 12-league covered set — see `config/covered_leagues.py`). Part C (confirmed-XI tier) does not proceed; it was explicitly gated on a Part B pass.

## The bar (verbatim, Phase 36 Task 5 / Phase 37 brief)

> Player/availability features justify a production build (covered leagues only) if, on a chronological holdout: btts or ou25 Brier improves by ≥0.005 with calibrated bin spread demonstrably widening; ou15 stays within 0.002 of baseline Brier; the improvement holds across ≥2 non-overlapping chronological holdout folds.

No threshold was touched after seeing results.

## What was tested

- **Training set:** 442 settled fixtures across the 12 covered leagues with complete availability features (both teams have prior-season `player_season_stats` and a fixture-keyed `injuries` lookup) — see `scripts/analysis/phase37_build_training_set.py`. 1,679 market-rows after the market join and the same corrupted-`our_prob` exclusion Track A applies (`v2/db_v2.py`).
- **Features:** home-minus-away difference of `absent_goal_share`, `absent_minutes_share`, `keeper_absent`, `n_regular_absences` — all computed from **prior-season** stats specifically to avoid the intra-season leakage a same-season aggregate would carry (see that script's docstring for the full reasoning).
- **Design:** 2 non-overlapping chronological walk-forward folds (Fold A: train on the earliest third, test on the middle third; Fold B: train on the first two-thirds, test on the final third). Baseline = logistic regression on `logit(our_prob)` alone (pure recalibration); treatment = baseline + the 4 features. This isolates whether availability data adds anything **beyond what the existing standing prediction already knows**, which is the actual question the bar asks.

## Results (`scripts/analysis/phase37_evaluate_bar.py`)

| Market | Fold A Brier Δ (treatment − baseline, + = better) | Fold A spread Δ | Fold B Brier Δ | Fold B spread Δ | Bar met? |
|---|---|---|---|---|---|
| btts | +0.0033 | +0.060 (widened) | **−0.0091** | n/a (baseline bin_spread undefined) | **FAIL** — sign flips between folds |
| ou25 | **−0.0049** | +0.063 | +0.0023 | n/a | **FAIL** — sign flips, neither fold clears +0.005 |
| ou15 (guard) | +0.0018 (OK) | — | **−0.0056** (worse than the −0.002 floor) | — | **VIOLATED** |
| h2h (secondary) | +0.0039 | +0.080 | −0.0121 | +0.056 | not gating, same sign-flip pattern |

Neither btts nor ou25 clears +0.005 in **both** folds — the required primary-market pass. The ou15 must-not-degrade guard is independently violated in Fold B.

## Is this signal or noise? (bootstrap check, `scripts/analysis/phase37_bootstrap_ci.py`)

At n≈140–150 per fold, 90% bootstrap CIs on the Brier delta are wide — 0.007 to 0.022 — and mostly straddle zero:

- btts: Fold A CI `[0.0000, +0.0068]`, Fold B CI `[−0.0199, +0.0021]` — no fold's CI clears +0.005 with the lower bound also positive.
- ou25: Fold A CI `[−0.0100, +0.0002]`, Fold B CI `[−0.0025, +00066]` — same story.
- **ou15 Fold B CI `[−0.0091, −0.0022]` — entirely below the −0.002 floor.** This one is not noise: the degradation is statistically real at this n, not a fluke of a small unlucky test split.

Honest reading: btts/ou25 show **no detectable signal in either direction** at this sample size — this FAIL is partly a genuine null and partly an underpowered test. ou15's degradation, however, is real. Since the bar requires the ou15 guard to hold regardless of what happens on the primary markets, the gate fails either way.

## Why "wait for more data" is not the right response here

Settled predictions in the covered set have accrued at ~5.3 fixtures/day (442 fixtures over the ~83-day span the prediction system has been live and settling in these leagues, 2026-04-20 to 2026-07-12 — this reflects how long our V2 pipeline has been running, not how long these leagues have played). Closing the CI width enough to reliably distinguish a 0.005 effect (roughly a 4–19x reduction depending on market, i.e. 16–360x more test-fold data) would take **years at current accrual**, not weeks. This is a genuine close, not a "check back next quarter."

## What's retained, what's not

- **Retained (no rebuild needed if this is ever revisited):** `config/covered_leagues.py` (empirically confirmed 12-league set, distinguishing lineup-only coverage too), the `injuries`-table extension (migration 032), and the 442-fixture leakage-safe training set / evaluation scripts. The `fetch_covered_injuries` job function and `scripts/covered_injuries_poll.py` are left intact but **not registered** in `backend/scheduler.py`'s job list — a FAIL means stop, and a live job quietly running for a closed lever is its own kind of loose end. Re-add the one line in `get_scheduler()` if this is ever deliberately revisited. Given the accrual-rate math above, that's a "years from now, if ever" revisit, not a standing collection effort.
- **Not built:** any change to the live standing-prediction pipeline. No `data_context` value was added for availability-adjusted predictions since nothing is being served. Part C (confirmed-XI tier, near-kickoff update, supersede-and-retain wiring, lead-time-segmented Track A) — not started, per the brief's explicit sequencing.
- **`lineups` / `lineup_players` tables:** schema exists (migration 032) but were never populated in bulk — that backfill was deliberately deferred pending this exact gate, and the gate came back FAIL, so it stays deferred indefinitely. Nothing to unwind.

## Long-tail statement (per the brief's standing requirement)

This closure applies to the 12-league covered set only — England/Russia PL, La Liga, MLS, Serie A (Italy + Brazil), Bundesliga, Ligue 1, Eredivisie, Süper Lig, Allsvenskan, Eliteserien. It says nothing new about the long tail (USL2, Serie D, Ethiopia, Azadegan, Gambia, etc.) where Phase 36 already found the provider has no lineup/injury data at all — that remains a hard no-go, unchanged, and unrelated to this result.

---

## Phase 37b addendum — injury `reason` taxonomy (what we hold)

Zero-API-call follow-up (2026-07-13): a dump of the `reason`/`type` taxonomy actually present in the 597-fixture covered-league injury backfill (7,933 rows with `fixture_id` set), for anyone considering reopening this lever later.

### Distinct `reason` values, by frequency

| reason | n | reason | n |
|---|---|---|---|
| Knee Injury | 1579 | Concussion | 24 |
| Injury | 878 | Head Injury | 21 |
| Inactive | 684 | Personal Reasons | 20 |
| Muscle Injury | 637 | Hand Injury | 20 |
| *(blank)* | 586 | Broken ankle | 18 |
| Yellow Cards | 470 | Arm Injury | 18 |
| Hamstring Injury | 338 | Surgery | 16 |
| Ankle Injury | 330 | Wrist Injury | 15 |
| Thigh Injury | 321 | Heel Injury | 14 |
| Leg Injury | 219 | Lacking Match Fitness | 13 |
| Red Card | 218 | Broken collarbone | 13 |
| Groin Injury | 182 | Loan agreement | 12 |
| Foot Injury | 124 | Convalescence | 11 |
| Calf Injury | 119 | Pelvis Injury | 8 |
| Achilles Tendon Injury | 117 | Broken shinbone | 8 |
| Lower-Body Injury | 114 | Finger Injury | 4 |
| Back Injury | 107 | Eye injury | 4 |
| Shoulder Injury | 99 | Broken nose | 4 |
| Suspended | 89 | Broken cheekbone | 4 |
| International duty | 85 | Broken Hand | 4 |
| Hip Injury | 76 | Stomach Disorder | 3 |
| Illness | 58 | Contusion | 3 |
| Broken Leg | 44 | Upper-Body Injury | 2 |
| Knock | 43 | Ribs Injury | 2 |
| Coach's decision | 33 | Overload | 2 |
| Toe Injury | 31 | Neck Injury | 2 |
| Health problems | 30 | Hernia | 2 |
| Elbow Injury | 26 | Face Injury | 2 |
| Rest | 25 | Abdominal strain | 2 |

### Category split

| Category | n | % |
|---|---|---|
| Typed injury (Hamstring, Knee, Knock, Illness, etc. — a specific medical/physical reason) | 5,374 | 67.7% |
| Ambiguous (`Inactive`, generic `Injury`, blank) | 1,594 | 20.1% |
| Non-injury absence (`Suspended`, `Yellow Cards`, `Red Card`, `International duty`, `Coach's decision`, `Personal Reasons`, `Loan agreement`, `Rest`, `Lacking Match Fitness`) | 965 | 12.2% |

Roughly a fifth of rows carry no usable signal at all beyond "this player didn't play" (`Inactive`/generic `Injury`/blank) — same information Track A's own lineup-vs-roster diff would already give for free, without a provider call.

### `type` field (Missing Fixture / Questionable) and co-occurrence with the category split

| type | n |
|---|---|
| Missing Fixture | 7,809 |
| Questionable | 124 |

| type | category | n |
|---|---|---|
| Missing Fixture | typed injury | 5,260 |
| Missing Fixture | ambiguous | 1,584 |
| Missing Fixture | non-injury absence | 965 |
| Questionable | typed injury | 114 |
| Questionable | ambiguous | 10 |

`Questionable` never co-occurs with a non-injury reason (suspensions/call-ups are always definite, never "questionable") — consistent with `type` tracking certainty-of-absence rather than cause. `type` is 98.4% `Missing Fixture`, so it carries almost no independent variance on its own in this dataset.

### What the data does NOT carry

No severity grade, no expected-return date, no recovery timeline of any kind — `reason` is a free-text/categorical label with no duration attached. Any recovery-time or return-date modeling would require **inference** (e.g. external per-injury-type duration priors), not a lookup this table can answer directly. This limitation is the reason for the design-if-reopened note below.

## Design-if-reopened note (not a plan to build — a record for if this is ever revisited)

Phase 37's tested features were **binary availability** (absent/present, per `phase37_build_training_set.py`) — a returned player was treated as 100% available from minute one, with no ramp-up.

A refinement identified during this closure, for the record, should this lever ever be revisited: **return-recency features** — time-since-return scaled by injury-type recovery priors (a hamstring, a knock, and an ACL tear do not carry the same expected layoff), a rushed-return flag (player returned materially earlier than the typical recovery window for that reason type), and a post-return performance-discount window. Rationale: early returns carry a documented performance discount and elevated re-injury hazard in sports-science literature, and a coach fielding a key player back ahead of a typical recovery schedule is itself fixture-relevant signal the binary features can't see.

What this would require beyond what Phase 37 had:
- **Per-injury-type recovery-duration priors** — either an external sports-science reference table, or duration learned from our own longitudinal absence spells (first `Missing Fixture` for a given reason to first fixture the player is back in a lineup) — a nontrivial data project on its own.
- **Return detection** — per-fixture minutes or lineup presence per player. Our player data is season-aggregate (`player_season_stats`), not per-fixture; `lineups`/`lineup_players` (migration 032) exist as schema for ~30 leagues but are schema-only and were never backfilled (see "What's retained" above) — there is currently no per-fixture presence signal to detect a "return" from at all.

**Verdict boundary, stated plainly:** this refinement is *more* data-hungry than the binary features that already failed the pre-registered bar on 442 fixtures with sign-flipping folds and a real (non-noise) `ou15` degradation. It does **not** reopen the lever at this provider, at this sample size. It becomes viable only via (a) a materially different data source — e.g. a provider that publishes actual expected-return dates, subject to the same ToS-check discipline already applied to the eloratings.net decision — or (b) years of accrual at the current ~5.3-fixtures/day rate. Either path is a **new lever** requiring its own bounded scoping probe and its own pre-registered bar before any training, exactly like Phase 36 did for this one. Do not retry the closed binary-availability test through this refinement dressed up as a variant.

Restating the footprint fact this interacts with: lineups (~30 leagues) and injuries (12 leagues) have different, non-overlapping coverage footprints (Phase 36/37). A lineup-only hypothesis — e.g. formation/rotation-intensity signal, which needs no injury data at all — remains a separate, unopened door with its own future bar; nothing in this closure evaluates or forecloses it.
