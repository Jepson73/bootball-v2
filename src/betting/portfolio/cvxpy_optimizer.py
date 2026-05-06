"""
CVXPY-based Markowitz Portfolio Optimizer

Dual-mode optimization:
- CVXPY with OSQP solver (optimal)
- Heuristic fallback (safe)

This module provides true mean-variance optimization without
breaking the system if cvxpy is unavailable.
"""

import logging
from typing import Optional, Tuple

import numpy as np
from numpy import inf

logger = logging.getLogger(__name__)

CVXPY_AVAILABLE = False
CVXPY_VERSION = None

try:
    import cvxpy as cp
    CVXPY_AVAILABLE = True
    CVXPY_VERSION = cp.__version__
    logger.info(f"CVXPY available: version {CVXPY_VERSION}")
except ImportError:
    logger.warning("CVXPY not available - using heuristic fallback")


class CVXPYMarkowitzOptimizer:
    """
    Markowitz Mean-Variance Optimization using CVXPY.
    
    Solves:
        max w.T * mu - gamma * w.T * Sigma * w
        s.t. sum(w) = 1, w >= 0, w <= max_weight
    
    Where:
        w = weight vector
        mu = expected returns
        Sigma = covariance matrix  
        gamma = risk aversion parameter
    """
    
    def __init__(self, max_weight: float = 0.5, solver: str = "OSQP"):
        self.max_weight = max_weight
        self.solver = solver
        self._last_status = "not_run"
        self._last_objective_value = None
        
        if not CVXPY_AVAILABLE:
            logger.warning("CVXPY not available - optimizer will use fallback")
    
    def optimize(
        self,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        risk_aversion: float = 1.0
    ) -> tuple:
        """
        Optimize portfolio using Markowitz mean-variance.
        
        Args:
            expected_returns: Array of expected returns
            cov_matrix: Covariance matrix of returns
            risk_aversion: Risk aversion parameter (higher = more conservative)
            
        Returns:
            Tuple of (weights, status)
        """
        n = len(expected_returns)
        
        if n == 0:
            return np.array([]), "empty_input"
        
        if n == 1:
            return np.array([1.0]), "single_asset"
        
        expected_returns = np.array(expected_returns, dtype=np.float64)
        expected_returns = np.nan_to_num(expected_returns, nan=0.0, posinf=1.0, neginf=-1.0)
        
        if cov_matrix.shape != (n, n):
            cov_matrix = np.eye(n)
            logger.warning(f"Invalid covariance matrix shape, using identity")
        
        # Clamp returns to stable range
        expected_returns = np.clip(expected_returns, -1.0, 1.0)
        expected_returns = np.nan_to_num(expected_returns, nan=0.0, posinf=1.0, neginf=-1.0)
        
        # Process covariance matrix
        cov_matrix = np.array(cov_matrix, dtype=np.float64)
        cov_matrix = np.nan_to_num(cov_matrix, nan=0.0, posinf=1.0, neginf=-1.0)
        
        # Make symmetric
        cov_matrix = (cov_matrix + cov_matrix.T) / 2
        
        # Ensure positive semi-definite
        min_eig = np.min(np.linalg.eigvals(cov_matrix))
        if min_eig < 1e-8:
            cov_matrix += np.eye(n) * max(1e-6, 1e-6 - min_eig)
            logger.debug("Covariance matrix regularized to ensure positive definiteness")
        
        max_w = self.max_weight
        min_weight = 0.0
        
        if not CVXPY_AVAILABLE:
            logger.warning("CVXPY not available, using heuristic fallback")
            return self._fallback_allocation(expected_returns, risk_aversion, max_w, min_weight), "fallback"
        
        solvers_tried = []
        
        for solver_name in ["OSQP", "SCS", "ECOS"]:
            try:
                w = cp.Variable(n)
                
                ret = expected_returns @ w
                risk = cp.quad_form(w, cov_matrix)
                
                objective = cp.Maximize(ret - risk_aversion * risk)
                
                # Use <= 1 instead of == 1 for more flexibility
                constraints = [
                    cp.sum(w) <= 1,
                    w >= min_weight,
                    w <= max_w
                ]
                
                problem = cp.Problem(objective, constraints)
                
                if solver_name == "OSQP":
                    solver_opts = {"max_iter": 3000, "eps_abs": 1e-6, "eps_rel": 1e-6, "adaptive_rho": True}
                    problem.solve(solver=cp.OSQP, **solver_opts)
                elif solver_name == "SCS":
                    problem.solve(solver=cp.SCS, max_iters=10000, verbose=False)
                else:
                    problem.solve(solver=cp.ECOS, max_iters=2000)
                
                solvers_tried.append(solver_name)
                
                if problem.status in ["optimal", "optimal_inaccurate"]:
                    self._last_status = "optimal"
                    self._last_objective_value = problem.objective.value
                    weights = np.array(w.value).flatten()
                    weights = np.nan_to_num(weights, nan=0.0)
                    
                    weights = np.clip(weights, min_weight, max_w)
                    total = np.sum(weights)
                    if total > 0:
                        weights = weights / total
                    else:
                        weights = np.ones(n) / n
                    
                    logger.info(f"CVXPY optimization succeeded with {solver_name}: {problem.status}")
                    return weights, self._last_status
                    
            except Exception as solver_error:
                logger.debug(f"Solver {solver_name} failed: {solver_error}")
                solvers_tried.append(f"{solver_name}_error")
                continue
        
        # All solvers failed - log diagnostics
        self._last_status = f"suboptimal_all_solvers_failed"
        logger.warning(f"All CVXPY solvers failed. Tried: {solvers_tried}")
        logger.warning(f"Returns: {expected_returns[:5]}..., Cov diagonal: {np.diag(cov_matrix)[:5]}...")
        
        # Fallback to heuristic
        weights = self._fallback_allocation(expected_returns, risk_aversion, max_w, min_weight)
        return weights, "fallback"
    
    def _fallback_allocation(self, expected_returns: np.ndarray, risk_aversion: float, max_w: float = 0.5, min_weight: float = 0.0) -> np.ndarray:
        """
        Simple heuristic allocation as fallback.
        
        Weights based on risk-adjusted expected returns.
        """
        n = len(expected_returns)
        
        if n == 0:
            return np.array([])
        
        if n == 1:
            return np.array([1.0])
        
        expected_returns = np.array(expected_returns, dtype=np.float64)
        expected_returns = np.nan_to_num(expected_returns, nan=0.0, posinf=1.0, neginf=-1.0)
        
        # Use risk-adjusted returns: return / (std + epsilon)
        if isinstance(expected_returns, np.ndarray) and len(expected_returns) > 1:
            std = np.std(expected_returns)
            if std > 0:
                risk_adjusted = expected_returns / (std + 0.01)
            else:
                risk_adjusted = expected_returns
        else:
            risk_adjusted = expected_returns
        
        positive_returns = np.maximum(risk_adjusted, 0)
        
        if np.sum(positive_returns) > 0:
            weights = positive_returns / np.sum(positive_returns)
        else:
            weights = np.ones(n) / n
        
        weights = np.clip(weights, min_weight, max_w)
        
        if np.sum(weights) > 0:
            weights = weights / np.sum(weights)
        else:
            weights = np.ones(n) / n
        
        logger.debug(f"Heuristic fallback: weights={weights[:5]}...")
        return weights
    
    @property
    def status(self) -> str:
        return self._last_status
    
    @property
    def last_objective_value(self) -> Optional[float]:
        return self._last_objective_value
    
    def get_metrics(self) -> dict:
        return {
            "cvxpy_available": CVXPY_AVAILABLE,
            "cvxpy_version": CVXPY_VERSION,
            "last_status": self._last_status,
            "last_objective_value": self._last_objective_value
        }


def create_optimizer(max_weight: float = 0.5) -> CVXPYMarkowitzOptimizer:
    """Factory function to create optimizer."""
    return CVXPYMarkowitzOptimizer(max_weight=max_weight)


def is_cvxpy_available() -> bool:
    """Check if cvxpy is available."""
    return CVXPY_AVAILABLE
