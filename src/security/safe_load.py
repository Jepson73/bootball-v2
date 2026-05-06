"""
Safe model loading utilities with HMAC integrity verification.

Two-layer defence:
  1. HMAC-SHA256 signature written alongside the pickle (.sig file).
  2. Signature verified before unpickling — tampered files are refused.

The signing key is derived from SECRET_KEY in settings.
If a .sig file is absent the load is allowed but logged as a warning
so that legacy models still work. Once a model is re-saved it will
always carry a signature.
"""

import hashlib
import hmac
import logging
import os
import pickle
from pathlib import Path

logger = logging.getLogger(__name__)


def _signing_key() -> bytes:
    """Return the HMAC signing key from settings."""
    try:
        from config.settings import settings
        return settings.secret_key.encode()
    except Exception:
        return b"bootball-default-key"


def _sig_path(model_path: str) -> str:
    return model_path + ".sig"


def _compute_hmac(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

def safe_model_load(path: str, default_return=None, verify_hmac: bool = True):
    """
    Load a pickled model from disk.

    If a .sig file exists alongside the model it is verified before
    unpickling.  If verification fails the load is aborted and
    default_return is returned.  If no .sig file exists the model is
    loaded with a warning (backward compatibility for unsigned legacy files).

    Args:
        path:          Path to the .pkl file.
        default_return: Value to return on any failure.
        verify_hmac:   Set False only in tests / migration tooling.

    Returns:
        Loaded model or default_return.
    """
    if not os.path.exists(path):
        logger.error("safe_model_load: file not found: %s", path)
        return default_return

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        logger.exception("safe_model_load: could not read %s", path)
        return default_return

    sig_file = _sig_path(path)
    if verify_hmac:
        if os.path.exists(sig_file):
            try:
                stored_sig = Path(sig_file).read_text().strip()
            except OSError:
                logger.exception("safe_model_load: could not read sig file %s", sig_file)
                return default_return

            expected = _compute_hmac(data, _signing_key())
            if not hmac.compare_digest(stored_sig, expected):
                logger.error(
                    "safe_model_load: HMAC verification FAILED for %s — refusing to load",
                    path,
                )
                return default_return
        else:
            logger.warning(
                "safe_model_load: no .sig file for %s — loading unsigned model "
                "(re-save to add HMAC protection)",
                path,
            )

    try:
        model = pickle.loads(data)  # noqa: S301 — data verified above
        logger.debug("safe_model_load: loaded %s", path)
        return model
    except Exception:
        logger.exception("safe_model_load: unpickling failed for %s", path)
        return default_return


def safe_model_save(model, path: str) -> bool:
    """
    Save a model to disk and write an HMAC-SHA256 signature alongside it.

    Always writes both .pkl and .sig atomically (sig written after data).

    Args:
        model: Model object to save.
        path:  Destination path.

    Returns:
        True on success, False on failure.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = pickle.dumps(model)

        with open(path, "wb") as f:
            f.write(data)

        sig = _compute_hmac(data, _signing_key())
        with open(_sig_path(path), "w") as f:
            f.write(sig)

        logger.debug("safe_model_save: saved %s + .sig", path)
        return True
    except Exception:
        logger.exception("safe_model_save: failed to save %s", path)
        return False


