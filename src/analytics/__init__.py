"""
Analytics module for model evaluation.

Provides offline analytics from event history:
- ModelEvaluator: Performance evaluation
- MarketAnalyzer: Market profitability analysis  
- ModelComparator: Compare model versions
"""

from src.analytics.model_evaluator import (
    ModelEvaluator,
    evaluate_model_performance,
    evaluate_run_performance,
)
from src.analytics.market_analysis import (
    MarketAnalyzer,
    analyze_market_performance,
    rank_markets_by_profitability,
)
from src.analytics.model_comparator import (
    ModelComparator,
    compare_model_versions,
    find_best_model,
)

__all__ = [
    "ModelEvaluator",
    "evaluate_model_performance",
    "evaluate_run_performance",
    "MarketAnalyzer",
    "analyze_market_performance", 
    "rank_markets_by_profitability",
    "ModelComparator",
    "compare_model_versions",
    "find_best_model",
]
