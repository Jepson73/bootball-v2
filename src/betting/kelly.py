"""
src/betting/kelly.py

Kelly Criterion for optimal bet sizing.

Formula:
  f* = (b*p - q) / b
  where:
    b = decimal_odd - 1  (net odds)
    p = our probability of winning
    q = 1 - p

Kelly maximises long-run bankroll growth but is aggressive.
Always use fractional Kelly (0.25× to 0.5×) in practice.

References:
  Kelly (1956) — A New Interpretation of Information Rate
  Thorp (1997) — The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market
"""


def kelly_fraction(our_prob: float, decimal_odd: float) -> float:
    """
    Returns full Kelly fraction (0.0 – 1.0) of bankroll to bet.
    Returns 0.0 if the bet has no edge.
    """
    b = decimal_odd - 1.0   # net odds
    p = our_prob
    q = 1.0 - p
    f = (b * p - q) / b
    return max(f, 0.0)


def fractional_kelly(our_prob: float, decimal_odd: float, fraction: float = 0.25) -> float:
    """
    Fractional Kelly — safer for real-world use where our prob estimates have error.
    Default: 0.25× (quarter-Kelly).
    """
    return kelly_fraction(our_prob, decimal_odd) * fraction


def kelly_stake(
    bankroll: float,
    our_prob: float,
    decimal_odd: float,
    fraction: float = 0.25,
    max_stake_pct: float = 0.05,
) -> float:
    """
    Returns recommended stake in currency units.
    Caps at max_stake_pct of bankroll regardless of Kelly output.
    """
    fk = fractional_kelly(our_prob, decimal_odd, fraction)
    raw = bankroll * fk
    capped = min(raw, bankroll * max_stake_pct)
    return round(capped, 2)
