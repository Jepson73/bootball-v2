"""
src/betting/ev.py

Expected Value calculator.
EV = (our_prob × decimal_odd) - 1
"""


def expected_value(our_prob: float, decimal_odd: float) -> float:
    """Expected value as a fraction (e.g. 0.05 = 5% edge)."""
    return our_prob * decimal_odd - 1.0


def implied_probability(decimal_odd: float) -> float:
    """Naive implied probability (1/odd), includes margin."""
    return 1.0 / decimal_odd