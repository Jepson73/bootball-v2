"""
src/security/rate_limit.py

Rate limiting for API endpoints.
"""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit."""
    max_requests: int      # Maximum requests per window
    window_seconds: float  # Time window in seconds
    key_prefix: str = "rl"  # Prefix for rate limit keys


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    remaining: int
    reset_at: float  # Unix timestamp when window resets
    retry_after: float | None = None  # Seconds until retry allowed


class SlidingWindowRateLimiter:
    """Sliding window rate limiter.

    Tracks requests per client using a sliding window algorithm.
    Memory-efficient: only stores timestamps for active windows.
    """

    def __init__(self, config: RateLimitConfig):
        self.config = config
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._cleanup()

    def _cleanup(self) -> None:
        """Remove expired timestamps."""
        now = time.time()
        cutoff = now - self.config.window_seconds
        for key in list(self._requests.keys()):
            self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]
            if not self._requests[key]:
                del self._requests[key]

    def _get_key(self, client_id: str) -> str:
        """Generate rate limit key for client."""
        return f"{self.config.key_prefix}:{client_id}"

    def check(self, client_id: str) -> RateLimitResult:
        """Check if client is within rate limit.

        Args:
            client_id: Unique identifier for the client (IP, user_id, etc.)

        Returns:
            RateLimitResult with allowed status and metadata
        """
        self._cleanup()

        now = time.time()
        key = self._get_key(client_id)
        window_start = now - self.config.window_seconds

        # Count requests in current window
        timestamps = self._requests[key]
        active_requests = [ts for ts in timestamps if ts > window_start]

        count = len(active_requests)
        remaining = max(0, self.config.max_requests - count)

        if count < self.config.max_requests:
            # Allow request and record timestamp
            active_requests.append(now)
            self._requests[key] = active_requests
            return RateLimitResult(
                allowed=True,
                remaining=remaining - 1,  # After this request
                reset_at=now + self.config.window_seconds,
            )
        else:
            # Deny request - calculate retry time
            oldest = min(active_requests)
            retry_after = oldest + self.config.window_seconds - now
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=oldest + self.config.window_seconds,
                retry_after=max(0.1, retry_after),
            )

    def clear(self, client_id: str | None = None) -> None:
        """Clear rate limit for client or all clients."""
        if client_id is None:
            self._requests.clear()
        else:
            key = self._get_key(client_id)
            if key in self._requests:
                del self._requests[key]


# Pre-configured rate limiters
DEFAULT_CONFIG = RateLimitConfig(max_requests=100, window_seconds=60, key_prefix="default")
AUTH_CONFIG = RateLimitConfig(max_requests=5, window_seconds=60, key_prefix="auth")
API_CONFIG = RateLimitConfig(max_requests=200, window_seconds=60, key_prefix="api")

# Global limiter instances
_default_limiter = SlidingWindowRateLimiter(DEFAULT_CONFIG)
_auth_limiter = SlidingWindowRateLimiter(AUTH_CONFIG)
_api_limiter = SlidingWindowRateLimiter(API_CONFIG)


def get_limiter(limit_type: str = "default") -> SlidingWindowRateLimiter:
    """Get a rate limiter by type."""
    limiters = {
        "default": _default_limiter,
        "auth": _auth_limiter,
        "api": _api_limiter,
    }
    return limiters.get(limit_type, _default_limiter)


def rate_limit(
    client_id: str,
    limit_type: str = "default",
) -> tuple[bool, dict]:
    """Check rate limit and return response headers.

    Args:
        client_id: Client identifier (IP, user_id)
        limit_type: Type of limit to apply

    Returns:
        Tuple of (allowed, headers_dict)
    """
    limiter = get_limiter(limit_type)
    result = limiter.check(client_id)

    headers = {
        "X-RateLimit-Limit": str(limiter.config.max_requests),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Reset": str(int(result.reset_at)),
    }

    if not result.allowed:
        headers["Retry-After"] = str(int(result.retry_after or 0))

    return result.allowed, headers


def get_client_id_from_request(request) -> str:
    """Extract client ID from Flask request.

    Uses X-Forwarded-For if behind proxy, else remote_addr.
    """
    # Check for proxy header
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take first IP in chain
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.remote_addr or "unknown"

    # Hash to prevent rate limit fingerprinting
    return hashlib.sha256(client_ip.encode()).hexdigest()[:16]
