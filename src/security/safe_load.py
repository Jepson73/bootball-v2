"""
Safe model loading utilities.

Provides safe wrappers for pickle.load to handle failures gracefully.
"""

import os
import pickle
import logging

logger = logging.getLogger(__name__)


def safe_model_load(path, default_return=None):
    """
    Safely load a model from disk using pickle.
    
    Args:
        path: Path to the model file
        default_return: Value to return on failure (default: None)
        
    Returns:
        Loaded model on success, default_return on failure
    """
    if not os.path.exists(path):
        logger.error(f"safe_model_load: File not found: {path}")
        return default_return
    
    try:
        with open(path, "rb") as f:
            model = pickle.load(f)
        logger.debug(f"safe_model_load: Successfully loaded: {path}")
        return model
    except Exception as e:
        logger.error(f"safe_model_load: Failed to load {path}: {e}")
        return default_return


def safe_model_save(model, path) -> bool:
    """
    Safely save a model to disk.
    
    Args:
        model: Model object to save
        path: Path to save to
        
    Returns:
        True on success, False on failure
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(model, f)
        logger.debug(f"safe_model_save: Successfully saved: {path}")
        return True
    except Exception as e:
        logger.error(f"safe_model_save: Failed to save {path}: {e}")
        return False
