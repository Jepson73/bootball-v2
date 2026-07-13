"""
scripts/analysis/phase37_evaluate_bar.py

Phase 37 Part B.3 — judge the availability-tier features against the
Phase 36 pre-registered bar, VERBATIM, no post-hoc threshold changes:

  "Player/availability features justify a production build (covered leagues
   only) if, on a chronological holdout:
     - btts or ou25 Brier improves by >=0.005, with calibrated bin spread
       demonstrably widening (resolution improving, not just reliability),
     - ou15 stays within 0.002 of baseline Brier (must-not-degrade),
     - the improvement holds across at least 2 non-overlapping chronological
       holdout folds."

Design: chronological walk-forward, 3 equal-count periods per market
(P1/P2/P3, sorted by fixture date). Fold A trains on P1, tests on P2. Fold B
trains on P1+P2, tests on P3 -- 2 non-overlapping holdouts, both strictly
after their own training data.

Baseline model:  logit(y) ~ logit(our_prob)                         [pure recalibration, no new info]
Treatment model: logit(y) ~ logit(our_prob) + availability features [Part B.1's 4 features]

This isolates the marginal contribution of availability features beyond
what the existing standing prediction already captures -- the actual
question the bar is asking, not "is a model with more inputs generically
better."

Missing diff_absent_goal_share / diff_absent_minutes_share (division guard:
team's prior-season total was 0) are imputed to 0 -- "no measurable
share of production absent" -- documented here, not silently dropped
(35%/8% of rows respectively; dropping would shrink n well below what
Phase 36 already flagged as thin).

Bin spread = max(actual_rate) - min(actual_rate) across the 10 calibration
bins (v2/db_v2.py's exact bin definition) with n>=5, i.e. the same
"resolution vs. reliability" read Track A already uses.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

CSV_PATH = Path(__file__).parent / "phase37_training_set.csv"
EPS = 1e-7
MARKETS = ["btts", "ou25", "ou15", "h2h"]
FEATURE_COLS = ["diff_absent_goal_share", "diff_absent_minutes_share", "diff_keeper_absent", "diff_n_regular_absences"]


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _load(market: str):
    rows = []
    with CSV_PATH.open() as f:
        for r in csv.DictReader(f):
            if r["market"] != market:
                continue
            feat = []
            for c in FEATURE_COLS:
                v = r[c]
                feat.append(float(v) if v != "" else 0.0)
            rows.append({
                "date": r["date"],
                "our_prob": float(r["our_prob"]),
                "won": int(r["won"]),
                "features": feat,
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def _brier(y_true, p_pred):
    p = np.clip(p_pred, EPS, 1 - EPS)
    return float(np.mean((p - y_true) ** 2))


def _bin_spread(y_true, p_pred, min_n=5):
    bins = defaultdict(list)
    for y, p in zip(y_true, p_pred):
        idx = min(int(p * 10), 9)
        bins[idx].append(y)
    actual_rates = [np.mean(v) for v in bins.values() if len(v) >= min_n]
    if len(actual_rates) < 2:
        return None
    return max(actual_rates) - min(actual_rates)


def _fit_predict(train, test, use_features: bool):
    X_train = _logit(np.array([r["our_prob"] for r in train])).reshape(-1, 1)
    X_test = _logit(np.array([r["our_prob"] for r in test])).reshape(-1, 1)
    if use_features:
        F_train = np.array([r["features"] for r in train])
        F_test = np.array([r["features"] for r in test])
        X_train = np.hstack([X_train, F_train])
        X_test = np.hstack([X_test, F_test])

    y_train = np.array([r["won"] for r in train])
    y_test = np.array([r["won"] for r in test])

    if len(np.unique(y_train)) < 2:
        return None  # degenerate fold, can't fit a classifier

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)
    p_test = model.predict_proba(X_test)[:, 1]
    return {
        "brier": _brier(y_test, p_test),
        "bin_spread": _bin_spread(y_test, p_test),
        "n": len(test),
    }


def evaluate_market(market: str) -> dict:
    rows = _load(market)
    n = len(rows)
    if n < 30:
        return {"market": market, "n": n, "status": "too_few_rows"}

    third = n // 3
    p1, p2, p3 = rows[:third], rows[third:2 * third], rows[2 * third:]

    folds = [
        ("A (train=P1, test=P2)", p1, p2),
        ("B (train=P1+P2, test=P3)", p1 + p2, p3),
    ]

    results = []
    for label, train, test in folds:
        baseline = _fit_predict(train, test, use_features=False)
        treatment = _fit_predict(train, test, use_features=True)
        if baseline is None or treatment is None:
            results.append({"fold": label, "status": "degenerate"})
            continue
        brier_delta = baseline["brier"] - treatment["brier"]  # positive = treatment better
        spread_delta = (
            (treatment["bin_spread"] - baseline["bin_spread"])
            if (treatment["bin_spread"] is not None and baseline["bin_spread"] is not None)
            else None
        )
        results.append({
            "fold": label,
            "n_test": test and len(test),
            "baseline_brier": baseline["brier"],
            "treatment_brier": treatment["brier"],
            "brier_delta": brier_delta,
            "baseline_bin_spread": baseline["bin_spread"],
            "treatment_bin_spread": treatment["bin_spread"],
            "spread_delta": spread_delta,
        })

    return {"market": market, "n": n, "folds": results}


def main():
    all_results = {m: evaluate_market(m) for m in MARKETS}

    print("=" * 100)
    for market, res in all_results.items():
        print(f"\nMarket: {market}  (n={res['n']})")
        if "folds" not in res:
            print(f"  {res.get('status')}")
            continue
        for f in res["folds"]:
            if f.get("status") == "degenerate":
                print(f"  Fold {f['fold']}: DEGENERATE (single-class train or test)")
                continue
            print(f"  Fold {f['fold']}: n_test={f['n_test']}")
            print(f"    baseline  brier={f['baseline_brier']:.4f}  bin_spread={f['baseline_bin_spread']}")
            print(f"    treatment brier={f['treatment_brier']:.4f}  bin_spread={f['treatment_bin_spread']}")
            print(f"    brier_delta (positive=treatment better) = {f['brier_delta']:+.4f}")
            print(f"    spread_delta (positive=treatment more resolved) = {f['spread_delta']}")
    print("=" * 100)

    # ── Verdict against the pre-registered bar (verbatim) ──────────────────
    print("\nVERDICT against Phase 36's pre-registered bar:\n")
    primary_pass = False
    for market in ["btts", "ou25"]:
        res = all_results[market]
        if "folds" not in res:
            continue
        fold_passes = []
        for f in res["folds"]:
            if f.get("status") == "degenerate":
                fold_passes.append(False)
                continue
            improves = f["brier_delta"] >= 0.005
            widens = f["spread_delta"] is not None and f["spread_delta"] > 0
            fold_passes.append(improves and widens)
        market_pass = all(fold_passes) and len(fold_passes) >= 2
        print(f"  {market}: fold results = {fold_passes} -> {'PASS' if market_pass else 'FAIL'} this market")
        primary_pass = primary_pass or market_pass

    ou15 = all_results["ou15"]
    ou15_ok = True
    if "folds" in ou15:
        for f in ou15["folds"]:
            if f.get("status") == "degenerate":
                continue
            degrade = -f["brier_delta"]  # positive means treatment worse than baseline
            if degrade > 0.002:
                ou15_ok = False
        print(f"  ou15 must-not-degrade (<=0.002 worse): {'OK' if ou15_ok else 'VIOLATED'}")

    print(f"\n  Overall: {'PASS' if (primary_pass and ou15_ok) else 'FAIL'}")


if __name__ == "__main__":
    main()
