"""
src/evaluation/calibration.py

Brier score and log loss for probabilistic predictions.
"""

import numpy as np


def brier_score(predictions: list[dict], actuals: list[str]) -> float:
    """Brier score (lower is better)."""
    total = 0.0
    for pred, actual in zip(predictions, actuals):
        probs = [pred.get("H", 0), pred.get("D", 0), pred.get("A", 0)]
        outcome_idx = {"H": 0, "D": 1, "A": 2}[actual]
        for i, p in enumerate(probs):
            o = 1 if i == outcome_idx else 0
            total += (p - o) ** 2
    return total / len(predictions)


def log_loss(predictions: list[dict], actuals: list[str]) -> float:
    """Log loss (lower is better)."""
    eps = 1e-15
    total = 0.0
    for pred, actual in zip(predictions, actuals):
        probs = [pred.get("H", 0), pred.get("D", 0), pred.get("A", 0)]
        outcome_idx = {"H": 0, "D": 1, "A": 2}[actual]
        p = max(min(probs[outcome_idx], 1 - eps), eps)
        total += -np.log(p)
    return total / len(predictions)