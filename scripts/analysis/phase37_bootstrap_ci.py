"""
scripts/analysis/phase37_bootstrap_ci.py

Phase 37 Part B.3 addendum -- bootstrap CI on the brier_delta (baseline -
treatment) for btts/ou25's Fold A and Fold B, to check whether the observed
sign-flipping deltas (Part B.3's headline result) are distinguishable from
noise at this n, or whether the FAIL verdict is really "no detectable signal
either way" (a power problem, informing what n Part B.4's closure note should
cite) rather than "detected and it's negative."

Resamples the TEST set with replacement (holding the fitted train-set models
fixed) 1000x per fold, recomputes brier_delta each time, reports the 90% CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from phase37_evaluate_bar import _load, _fit_predict, _brier, _logit, EPS
from sklearn.linear_model import LogisticRegression

N_BOOT = 1000
RNG = np.random.default_rng(37)


def _fit(train, use_features: bool):
    X = _logit(np.array([r["our_prob"] for r in train])).reshape(-1, 1)
    if use_features:
        F = np.array([r["features"] for r in train])
        X = np.hstack([X, F])
    y = np.array([r["won"] for r in train])
    m = LogisticRegression(max_iter=1000)
    m.fit(X, y)
    return m


def _predict(model, rows, use_features: bool):
    X = _logit(np.array([r["our_prob"] for r in rows])).reshape(-1, 1)
    if use_features:
        F = np.array([r["features"] for r in rows])
        X = np.hstack([X, F])
    return model.predict_proba(X)[:, 1]


def bootstrap_market(market: str):
    rows = _load(market)
    n = len(rows)
    third = n // 3
    p1, p2, p3 = rows[:third], rows[third:2 * third], rows[2 * third:]
    folds = [("A", p1, p2), ("B", p1 + p2, p3)]

    for label, train, test in folds:
        if len(np.unique([r["won"] for r in train])) < 2:
            print(f"  {market} fold {label}: degenerate train, skipping")
            continue
        base_model = _fit(train, use_features=False)
        treat_model = _fit(train, use_features=True)
        p_base = _predict(base_model, test, use_features=False)
        p_treat = _predict(treat_model, test, use_features=True)
        y = np.array([r["won"] for r in test])

        deltas = []
        idx = np.arange(len(test))
        for _ in range(N_BOOT):
            sample = RNG.choice(idx, size=len(idx), replace=True)
            b = _brier(y[sample], p_base[sample])
            t = _brier(y[sample], p_treat[sample])
            deltas.append(b - t)
        deltas = np.array(deltas)
        lo, hi = np.percentile(deltas, [5, 95])
        point = np.mean(deltas)
        print(f"  {market} fold {label} (n_test={len(test)}): brier_delta point={point:+.4f}  90% CI=[{lo:+.4f}, {hi:+.4f}]  width={hi-lo:.4f}")


def main():
    for market in ["btts", "ou25", "ou15"]:
        print(f"\nMarket: {market}")
        bootstrap_market(market)


if __name__ == "__main__":
    main()
