"""
tests/integration/test_safe_load.py

Tests for HMAC-signed pickle save/load.

Run: pytest tests/integration/test_safe_load.py -v
"""
import sys
sys.path.insert(0, ".")

import os
import pickle
import pytest
import tempfile
from pathlib import Path

from src.security.safe_load import (
    safe_model_load,
    safe_model_save,
    _compute_hmac,
    _signing_key,
    _sig_path,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_model_path(tmp_path):
    return str(tmp_path / "model_test.pkl")


# ── Round-trip ─────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_save_and_load_simple_object(self, tmp_model_path):
        model = {"weights": [0.1, 0.2, 0.3], "version": "1.0"}
        ok = safe_model_save(model, tmp_model_path)
        assert ok is True
        loaded = safe_model_load(tmp_model_path)
        assert loaded == model

    def test_save_creates_sig_file(self, tmp_model_path):
        safe_model_save({"x": 1}, tmp_model_path)
        assert os.path.exists(_sig_path(tmp_model_path))

    def test_sig_file_contains_hex_digest(self, tmp_model_path):
        safe_model_save({"x": 1}, tmp_model_path)
        sig = Path(_sig_path(tmp_model_path)).read_text().strip()
        assert len(sig) == 64  # SHA-256 → 64 hex chars
        assert all(c in "0123456789abcdef" for c in sig)

    def test_load_various_types(self, tmp_path):
        objects = [
            42,
            "hello",
            [1, 2, 3],
            {"a": 1},
            (True, None, 3.14),
        ]
        for i, obj in enumerate(objects):
            path = str(tmp_path / f"model_{i}.pkl")
            safe_model_save(obj, path)
            loaded = safe_model_load(path)
            assert loaded == obj


# ── Integrity checks ───────────────────────────────────────────────────────────

class TestIntegrity:
    def test_tampered_pickle_refused(self, tmp_model_path):
        safe_model_save({"secret": "data"}, tmp_model_path)

        # Corrupt the pickle bytes
        with open(tmp_model_path, "rb") as f:
            data = f.read()
        with open(tmp_model_path, "wb") as f:
            f.write(data[:-4] + b"XXXX")  # overwrite last 4 bytes

        result = safe_model_load(tmp_model_path)
        assert result is None

    def test_tampered_sig_refused(self, tmp_model_path):
        safe_model_save({"ok": True}, tmp_model_path)
        with open(_sig_path(tmp_model_path), "w") as f:
            f.write("a" * 64)  # wrong signature

        result = safe_model_load(tmp_model_path)
        assert result is None

    def test_missing_sig_warns_but_loads(self, tmp_model_path):
        # Write a valid pickle without a sig file (legacy model)
        with open(tmp_model_path, "wb") as f:
            pickle.dump({"legacy": True}, f)

        result = safe_model_load(tmp_model_path)
        assert result == {"legacy": True}

    def test_verify_false_skips_hmac(self, tmp_model_path):
        safe_model_save({"x": 1}, tmp_model_path)
        with open(_sig_path(tmp_model_path), "w") as f:
            f.write("bad" * 20)  # wrong sig

        # Should still load when verification disabled
        result = safe_model_load(tmp_model_path, verify_hmac=False)
        assert result == {"x": 1}

    def test_missing_file_returns_default(self, tmp_model_path):
        result = safe_model_load(tmp_model_path, default_return="MISSING")
        assert result == "MISSING"


# ── HMAC primitives ────────────────────────────────────────────────────────────

class TestHMACPrimitives:
    def test_same_data_same_key_deterministic(self):
        data = b"test payload"
        key = b"test-key"
        assert _compute_hmac(data, key) == _compute_hmac(data, key)

    def test_different_data_different_digest(self):
        key = b"test-key"
        h1 = _compute_hmac(b"data A", key)
        h2 = _compute_hmac(b"data B", key)
        assert h1 != h2

    def test_different_key_different_digest(self):
        data = b"same data"
        h1 = _compute_hmac(data, b"key A")
        h2 = _compute_hmac(data, b"key B")
        assert h1 != h2

    def test_signing_key_is_bytes(self):
        key = _signing_key()
        assert isinstance(key, bytes)
        assert len(key) > 0
