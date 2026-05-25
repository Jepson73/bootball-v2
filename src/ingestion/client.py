"""
src/ingestion/client.py

Rate-limited, cache-aware API-Football client.
Every endpoint from the API docs is represented here.
Never hits the API for data already in the local cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

logger = logging.getLogger(__name__)

# Raw response cache directory
CACHE_DIR = Path("data/raw/api_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Daily call counter file
COUNTER_FILE = Path("data/raw/.api_call_count.json")


def _cache_key(endpoint: str, params: dict) -> str:
    """Stable hash of endpoint + sorted params."""
    raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str) -> dict | None:
    p = _cache_path(key)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _write_cache(key: str, data: dict) -> None:
    _cache_path(key).write_text(json.dumps(data))


def _load_counter() -> dict:
    today = time.strftime("%Y-%m-%d")
    if COUNTER_FILE.exists():
        try:
            text = COUNTER_FILE.read_text().strip()
            if text:
                c = json.loads(text)
                if c.get("date") == today:
                    return c
        except (json.JSONDecodeError, OSError):
            pass  # truncated mid-write or corrupt — reset below
    return {"date": today, "count": 0}


def _increment_counter() -> int:
    c = _load_counter()
    c["count"] += 1
    # Atomic write: write to temp then rename so readers never see a partial file
    tmp = COUNTER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c))
    tmp.replace(COUNTER_FILE)
    return c["count"]


def calls_used_today() -> int:
    return _load_counter()["count"]


def calls_remaining_today() -> int:
    return settings.api_calls_per_day - calls_used_today()


_api_status_cache: dict = {"data": None, "fetched_at": 0.0}
_API_STATUS_TTL = 120  # refresh real quota at most every 2 minutes


def get_api_status() -> dict:
    """Fetch real quota from the API /status endpoint (does not cost a call).

    Returns dict with keys: plan, used, limit, remaining, active.
    Falls back to local counter on failure.
    Cached for 2 minutes so repeated calls don't spam the endpoint.
    """
    global _api_status_cache
    now = time.monotonic()
    if _api_status_cache["data"] and now - _api_status_cache["fetched_at"] < _API_STATUS_TTL:
        return _api_status_cache["data"]

    result: dict = {}
    try:
        session = requests.Session()
        session.headers.update({"x-apisports-key": settings.api_football_key})
        resp = session.get(
            f"{settings.api_football_base_url}/status", timeout=10
        )
        resp.raise_for_status()
        body = resp.json()
        r = body.get("response", {})
        sub = r.get("subscription", {})
        req = r.get("requests", {})
        result = {
            "plan": sub.get("plan", "Unknown"),
            "active": sub.get("active", False),
            "used": req.get("current", 0),
            "limit": req.get("limit_day", settings.api_calls_per_day),
            "remaining": req.get("limit_day", settings.api_calls_per_day) - req.get("current", 0),
            "source": "api",
        }
    except Exception as e:
        logger.warning("get_api_status fallback to local counter: %s", e)
        used = calls_used_today()
        result = {
            "plan": "Unknown",
            "active": True,
            "used": used,
            "limit": settings.api_calls_per_day,
            "remaining": settings.api_calls_per_day - used,
            "source": "local",
        }

    _api_status_cache["data"] = result
    _api_status_cache["fetched_at"] = now
    return result


class APIFootballClient:
    """
    Thin, cache-first wrapper over API-Football v3.

    Usage:
        client = APIFootballClient()
        fixtures = client.get_fixtures(league_id=39, season=2024, status="FT")
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "x-apisports-key": settings.api_football_key,
        })
        self._last_call_at: float = 0.0

    # ── Internal ──────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        gap = settings.api_interval_seconds
        if elapsed < gap:
            time.sleep(gap - elapsed)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _request(self, endpoint: str, params: dict) -> dict:
        """Raw HTTP call (no caching). Retries on transient errors."""
        if calls_remaining_today() <= 0:
            raise RuntimeError(
                f"Daily API budget exhausted ({settings.api_calls_per_day} calls used)."
            )

        self._throttle()
        url = f"{settings.api_football_base_url}/{endpoint}"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        self._last_call_at = time.monotonic()
        count = _increment_counter()
        logger.debug("API call #%d → %s %s", count, endpoint, params)
        return resp.json()

    def get(
        self,
        endpoint: str,
        params: dict,
        *,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Cache-first GET. Returns the `response` list from the API payload."""
        key = _cache_key(endpoint, params)
        if not force_refresh:
            cached = _read_cache(key)
            if cached is not None:
                logger.debug("Cache hit: %s %s", endpoint, params)
                return cached.get("response", [])

        if settings.dry_run:
            logger.warning("DRY RUN — skipping live call: %s %s", endpoint, params)
            return []

        data = self._request(endpoint, params)
        _write_cache(key, data)

        errors = data.get("errors", {})
        if errors:
            logger.warning("API errors for %s %s: %s", endpoint, params, errors)

        return data.get("response", [])

    # ── Endpoints (all from API_REQUESTS.md) ─────────────────────────────

    def get_leagues(self, country: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if country:
            params["country"] = country
        return self.get("leagues", params)

    def get_teams(
        self,
        league_id: int | None = None,
        season: int | None = None,
        country: str | None = None,
        team_id: int | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if league_id:
            params["league"] = league_id
        if season:
            params["season"] = season
        if country:
            params["country"] = country
        if team_id:
            params["id"] = team_id
        return self.get("teams", params)

    def get_teams_countries(self) -> list[dict]:
        """All countries (~231 calls total if iterated). Step 1 of bulk team fetch."""
        return self.get("countries", {})

    def get_fixtures(
        self,
        league_id: int | None = None,
        season: int | None = None,
        team_id: int | None = None,
        fixture_id: int | None = None,
        ids: str | None = None,          # "id1-id2-id3" for batch (max 20)
        date: str | None = None,          # "YYYY-MM-DD"
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,        # "FT", "NS", "1H", etc.
        force_refresh: bool = False,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if league_id:
            params["league"] = league_id
        if season:
            params["season"] = season
        if team_id:
            params["team"] = team_id
        if fixture_id:
            params["id"] = fixture_id
        if ids:
            params["ids"] = ids
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if status:
            params["status"] = status
        return self.get("fixtures", params, force_refresh=force_refresh)

    def get_fixtures_batch(self, fixture_ids: list[int]) -> list[dict]:
        """
        Efficient batch fetch: up to 20 IDs per call.
        Returns all fixture data (events, lineups, stats, players) in one go.
        """
        results: list[dict] = []
        chunks = [fixture_ids[i:i + 20] for i in range(0, len(fixture_ids), 20)]
        for chunk in chunks:
            ids_param = "-".join(str(fid) for fid in chunk)
            results.extend(self.get_fixtures(ids=ids_param))
        return results

    def get_fixture_statistics(self, fixture_id: int) -> list[dict]:
        return self.get("fixtures/statistics", {"fixture": fixture_id})

    def get_fixture_events(self, fixture_id: int) -> list[dict]:
        return self.get("fixtures/events", {"fixture": fixture_id})

    def get_lineups(self, fixture_id: int) -> list[dict]:
        return self.get("fixtures/lineups", {"fixture": fixture_id})

    def get_team_statistics(
        self,
        team_id: int,
        league_id: int,
        season: int,
    ) -> list[dict]:
        return self.get(
            "teams/statistics",
            {"team": team_id, "league": league_id, "season": season},
        )

    def get_standings(self, league_id: int, season: int) -> list[dict]:
        """Get standings. Note: standings are nested inside 'league' object in response."""
        key = _cache_key("standings", {"league": league_id, "season": season})
        cached = _read_cache(key)
        if cached is not None:
            return self._extract_standings(cached)

        if settings.dry_run:
            return []

        data = self._request("standings", {"league": league_id, "season": season})
        _write_cache(key, data)
        return self._extract_standings(data)

    def _extract_standings(self, data: dict) -> list[dict]:
        """
        Extract standings from API response.
        API returns either:
          1. Flat list: [{"rank": 1, "team": {...}, "all": {...}}, ...]
          2. Nested: [{"league": {"standings": [[{...}, ...]]}}]
        """
        response = data.get("response", [])
        standings_list = []

        for item in response:
            if not isinstance(item, dict):
                continue

            if "rank" in item and "team" in item:
                standings_list.append(item)
            elif "league" in item:
                league_obj = item.get("league", {})
                stands = league_obj.get("standings", [])
                for group in stands:
                    if isinstance(group, list):
                        standings_list.extend(group)

        return standings_list

    def get_odds(
            self,
            fixture_id: int | None = None,
            league_id: int | None = None,
            season: int | None = None,
            bet_type: str | int = 1,
        ) -> list[dict]:
        bet_id = {"h2h": 1, "btts": 8, "over_under": 5}.get(str(bet_type), bet_type)
        params: dict[str, Any] = {"bet": bet_id}
        if fixture_id:
            params["fixture"] = fixture_id
        if league_id:
            params["league"] = league_id
        if season:
            params["season"] = season
        return self.get("odds", params)

    def get_injuries(self, league_id: int, date: str) -> list[dict]:
        return self.get("injuries", {"league": league_id, "date": date})

    def get_players(self, team_id: int, season: int) -> list[dict]:
        """Get player statistics for a team."""
        return self.get("players", {"team": team_id, "season": season})

    def get_player(self, player_id: int) -> list[dict]:
        """Get single player details."""
        return self.get("players/player", {"player": player_id})

    def get_predictions(self, fixture_id: int) -> list[dict]:
        """API-Football's own prediction — use as one signal, not ground truth."""
        return self.get("predictions", {"fixture": fixture_id})

    def get_head2head(self, team1_id: int, team2_id: int) -> list[dict]:
        return self.get("fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}"})

    # ── Status ─────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Returns account info including remaining API calls."""
        return self._request("status", {})
