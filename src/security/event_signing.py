"""
src/security/event_signing.py

Event signing for handler verification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from config.settings import settings


@dataclass
class SignedEvent:
    """Event with signature metadata."""
    payload: dict
    signature: str
    timestamp: float
    sequence: int


class EventSigner:
    """Signs and verifies event payloads.

    Uses HMAC-SHA256 for signature generation.
    Includes timestamp and sequence for replay protection.
    """

    def __init__(self, secret_key: str | None = None):
        self.secret_key = secret_key or getattr(settings, 'event_signing_key', 'dev-secret-change-me')
        self._sequence: int = 0
        self._last_signature: str | None = None

    def _compute_signature(self, payload: str, timestamp: float, sequence: int) -> str:
        """Compute HMAC signature."""
        message = f"{timestamp}:{sequence}:{payload}"
        signature = hmac.new(
            self.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    def sign(self, payload: dict) -> SignedEvent:
        """Sign an event payload.

        Args:
            payload: Event payload dict

        Returns:
            SignedEvent with signature, timestamp, sequence
        """
        self._sequence += 1
        timestamp = time.time()
        payload_str = json.dumps(payload, sort_keys=True, default=str)

        signature = self._compute_signature(payload_str, timestamp, self._sequence)
        self._last_signature = signature

        return SignedEvent(
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            sequence=self._sequence,
        )

    def verify(self, signed_event: SignedEvent) -> bool:
        """Verify an event signature.

        Args:
            signed_event: Event with signature to verify

        Returns:
            True if signature is valid and not replayed
        """
        # Check timestamp is recent (within 5 minutes)
        now = time.time()
        age = now - signed_event.timestamp
        if age > 300:  # 5 minutes
            return False

        # Compute expected signature
        payload_str = json.dumps(signed_event.payload, sort_keys=True, default=str)
        expected = self._compute_signature(
            payload_str,
            signed_event.timestamp,
            signed_event.sequence
        )

        # Compare signatures (constant-time)
        if not hmac.compare_digest(expected, signed_event.signature):
            return False

        # Check sequence is not too old (replay protection)
        if signed_event.sequence < self._sequence - 10000:
            # Sequence is very old, likely replay attack
            return False

        return True

    def reset_sequence(self) -> None:
        """Reset sequence counter (for testing or restart)."""
        self._sequence = 0
        self._last_signature = None


class EventVerifier:
    """Verifies signed events before processing."""

    def __init__(self, signer: EventSigner | None = None):
        self.signer = signer or EventSigner()

    def verify_or_raise(self, signed_event: SignedEvent) -> None:
        """Verify event or raise exception.

        Raises:
            ValueError: If signature invalid or replay detected
        """
        if not self.signer.verify(signed_event):
            raise ValueError("Invalid event signature or replay detected")

    def verify_dict(self, data: dict) -> tuple[bool, str]:
        """Verify event from dict format.

        Returns:
            Tuple of (valid, error_message)
        """
        try:
            signed_event = SignedEvent(
                payload=data.get("payload", {}),
                signature=data.get("signature", ""),
                timestamp=float(data.get("timestamp", 0)),
                sequence=int(data.get("sequence", 0)),
            )
            if self.signer.verify(signed_event):
                return True, ""
            return False, "Invalid signature"
        except Exception as e:
            return False, str(e)


# Global verifier instance
_verifier = EventVerifier()


def get_verifier() -> EventVerifier:
    """Get the global event verifier."""
    return _verifier


def verify_event(signed_event: SignedEvent) -> bool:
    """Quick verify function using global verifier."""
    return _verifier.signer.verify(signed_event)
