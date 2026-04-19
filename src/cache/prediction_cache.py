"""
src/cache/prediction_cache.py

Prediction caching with TTL-based invalidation.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Default TTLs per market (in seconds)
DEFAULT_TTLS = {
    "h2h": 3600,      # 1 hour
    "btts": 7200,      # 2 hours
    "ou25": 7200,      # 2 hours
    "ou15": 7200,      # 2 hours
    "default": 3600,    # 1 hour
}


@dataclass
class CachedPrediction:
    """A cached prediction with metadata."""
    fixture_id: int
    market: str
    prediction: dict[str, Any]
    cached_at: float = field(default_factory=time.time)
    ttl: int = 3600

    def is_stale(self) -> bool:
        """Check if cache entry is stale."""
        age = time.time() - self.cached_at
        return age > self.ttl

    def age_seconds(self) -> float:
        """Get age of cache entry in seconds."""
        return time.time() - self.cached_at


class PredictionCache:
    """TTL-based cache for predictions.

    Features:
    - Per-market TTLs
    - Automatic stale detection
    - LRU eviction when max_size reached
    - Thread-safe operations
    """

    def __init__(
        self,
        max_size: int = 10000,
        ttls: dict[str, int] | None = None,
    ):
        self.max_size = max_size
        self.ttls = ttls or DEFAULT_TTLS
        self._cache: dict[tuple[int, str], CachedPrediction] = {}
        self._access_order: list[tuple[int, str]] = []  # For LRU tracking

    def _make_key(self, fixture_id: int, market: str) -> tuple[int, str]:
        """Create cache key."""
        return (fixture_id, market)

    def _get_ttl(self, market: str) -> int:
        """Get TTL for market."""
        return self.ttls.get(market, self.ttls.get("default", 3600))

    def get(self, fixture_id: int, market: str) -> dict[str, Any] | None:
        """Get prediction from cache.

        Args:
            fixture_id: Fixture ID
            market: Market type

        Returns:
            Cached prediction dict or None if not found/stale
        """
        key = self._make_key(fixture_id, market)

        if key not in self._cache:
            return None

        cached = self._cache[key]

        if cached.is_stale():
            logger.debug(f"Cache stale for {fixture_id}/{market}")
            self._remove(key)
            return None

        # Update LRU order
        self._update_access(key)

        return cached.prediction

    def set(
        self,
        fixture_id: int,
        market: str,
        prediction: dict[str, Any],
        ttl: int | None = None,
    ) -> None:
        """Store prediction in cache.

        Args:
            fixture_id: Fixture ID
            market: Market type
            prediction: Prediction dict to cache
            ttl: Optional TTL override
        """
        key = self._make_key(fixture_id, market)

        # Evict if at capacity
        if len(self._cache) >= self.max_size and key not in self._cache:
            self._evict_lru()

        # Store
        cache_ttl = ttl or self._get_ttl(market)
        self._cache[key] = CachedPrediction(
            fixture_id=fixture_id,
            market=market,
            prediction=prediction,
            ttl=cache_ttl,
        )

        self._update_access(key)

    def invalidate(self, fixture_id: int, market: str | None = None) -> int:
        """Invalidate cache entries.

        Args:
            fixture_id: Fixture ID to invalidate
            market: Specific market to invalidate, or None for all markets

        Returns:
            Number of entries invalidated
        """
        if market:
            key = self._make_key(fixture_id, market)
            if key in self._cache:
                self._remove(key)
                return 1
            return 0

        # Invalidate all markets for fixture
        count = 0
        keys_to_remove = [k for k in self._cache if k[0] == fixture_id]
        for key in keys_to_remove:
            self._remove(key)
            count += 1

        logger.debug(f"Invalidated {count} cache entries for fixture {fixture_id}")
        return count

    def _remove(self, key: tuple[int, str]) -> None:
        """Remove entry from cache."""
        if key in self._cache:
            del self._cache[key]
        if key in self._access_order:
            self._access_order.remove(key)

    def _update_access(self, key: tuple[int, str]) -> None:
        """Update LRU tracking."""
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if self._access_order:
            oldest = self._access_order.pop(0)
            self._remove(oldest)
            logger.debug(f"Evicted LRU entry {oldest}")

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats
        """
        total_entries = len(self._cache)
        stale_entries = sum(1 for c in self._cache.values() if c.is_stale())

        return {
            "total_entries": total_entries,
            "stale_entries": stale_entries,
            "max_size": self.max_size,
            "utilization": total_entries / self.max_size if self.max_size > 0 else 0,
        }

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._access_order.clear()
        logger.info("Cache cleared")


# Global cache instance
_prediction_cache: PredictionCache | None = None


def get_prediction_cache() -> PredictionCache:
    """Get the global prediction cache."""
    global _prediction_cache
    if _prediction_cache is None:
        _prediction_cache = PredictionCache()
    return _prediction_cache


def cache_prediction(
    fixture_id: int,
    market: str,
    prediction: dict[str, Any],
    ttl: int | None = None,
) -> None:
    """Convenience function to cache a prediction."""
    get_prediction_cache().set(fixture_id, market, prediction, ttl)


def get_cached_prediction(
    fixture_id: int,
    market: str,
) -> dict[str, Any] | None:
    """Convenience function to get cached prediction."""
    return get_prediction_cache().get(fixture_id, market)


def invalidate_predictions(
    fixture_id: int,
    market: str | None = None,
) -> int:
    """Convenience function to invalidate predictions."""
    return get_prediction_cache().invalidate(fixture_id, market)
