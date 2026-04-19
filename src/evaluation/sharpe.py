# src/evaluation/sharpe.py - Risk-adjusted return metrics
"""
Sharpe ratio and risk metrics for betting strategy evaluation.

Metrics:
- Sharpe ratio: risk-adjusted return
- Maximum drawdown
- Volatility of returns
- Sortino ratio (downside risk only)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class RiskMetrics:
    """Risk-adjusted performance metrics."""
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    avg_return: float
    n_periods: int


def calculate_returns(pnl_history: List[float]) -> np.ndarray:
    """Convert PnL history to return series."""
    return np.array(pnl_history)


def sharpe_ratio(returns: np.ndarray, risk_free_rate: float = 0.0) -> float:
    """Calculate Sharpe ratio (annualized)."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    
    excess = returns.mean() - risk_free_rate
    return (excess / returns.std()) * np.sqrt(252)  # Annualize


def sortino_ratio(returns: np.ndarray, risk_free_rate: float = 0.0) -> float:
    """Calculate Sortino ratio (downside risk only)."""
    if len(returns) < 2:
        return 0.0
    
    excess = returns.mean() - risk_free_rate
    downside = returns[returns < 0]
    
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    
    return (excess / downside.std()) * np.sqrt(252)


def max_drawdown(cumulative_returns: np.ndarray) -> tuple[float, int]:
    """Calculate maximum drawdown and its index."""
    running_max = np.maximum.accumulate(cumulative_returns)
    drawdown = running_max - cumulative_returns
    
    max_idx = np.argmax(drawdown)
    max_dd = drawdown[max_idx]
    
    return max_dd, max_idx


def max_drawdown_pct(returns: np.ndarray) -> float:
    """Calculate max drawdown as percentage."""
    if len(returns) == 0:
        return 0.0
    cumulative = (1 + np.clip(returns, -0.5, 1.0)).cumprod()
    cumulative = np.clip(cumulative, 0.01, 1000)  # Prevent overflow
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (running_max - cumulative) / np.maximum(running_max, 0.01)
    
    return drawdown.max() if len(drawdown) > 0 else 0.0


def risk_metrics_from_pnl(pnl_history: List[float]) -> RiskMetrics:
    """
    Calculate all risk metrics from PnL history.
    
    Args:
        pnl_history: List of profit/loss values for each bet/period
    
    Returns:
        RiskMetrics with all computed values
    """
    if not pnl_history:
        return RiskMetrics(
            total_return=0.0,
            annualized_return=0.0,
            volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            avg_return=0.0,
            n_periods=0,
        )
    
    returns = np.array(pnl_history)
    n = len(returns)
    
    # Basic stats
    total_return = returns.sum()
    avg_return = returns.mean()
    volatility = returns.std()
    
    # Annualized (assuming ~380 matches/year, daily-ish)
    periods_per_year = 380
    annualized_return = (avg_return * periods_per_year)
    annualized_vol = volatility * np.sqrt(periods_per_year)
    
    # Sharpe
    sharpe = sharpe_ratio(returns) if annualized_vol > 0 else 0.0
    
    # Sortino
    sortino = sortino_ratio(returns) if volatility > 0 else 0.0
    
    # Drawdown
    max_dd, _ = max_drawdown(returns.cumsum())
    max_dd_pct = max_drawdown_pct(returns)
    
    return RiskMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        volatility=annualized_vol,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        avg_return=avg_return,
        n_periods=n,
    )


def print_risk_metrics(metrics: RiskMetrics) -> None:
    """Print risk metrics in readable format."""
    print(f"\n{'='*50}")
    print("RISK METRICS")
    print(f"{'='*50}")
    print(f"Total Return:       ${metrics.total_return:.2f}")
    print(f"Annualized Return:  {metrics.annualized_return*100:.2f}%")
    print(f"Volatility:         {metrics.volatility*100:.2f}%")
    print(f"Sharpe Ratio:       {metrics.sharpe_ratio:.3f}")
    print(f"Sortino Ratio:      {metrics.sortino_ratio:.3f}")
    print(f"Max Drawdown:       ${metrics.max_drawdown:.2f}")
    print(f"Max Drawdown %:     {metrics.max_drawdown_pct*100:.2f}%")
    print(f"Avg Return/Bet:     ${metrics.avg_return:.4f}")
    print(f"Number of Bets:     {metrics.n_periods}")
    print(f"{'='*50}\n")