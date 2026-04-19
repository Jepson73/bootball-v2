"""
src/security/validation.py

Input validation for all external data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationResult:
    """Result of a validation check."""
    valid: bool
    error: str | None = None
    sanitized: Any = None


def sanitize_string(value: str | None, max_length: int = 255) -> str:
    """Sanitize a string to prevent XSS and injection."""
    if value is None:
        return ""

    # Remove null bytes
    value = value.replace("\x00", "")

    # Strip control characters except newlines (which we'll handle separately)
    value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)

    # Limit length
    value = value[:max_length]

    return value


def sanitize_for_html(value: str | None) -> str:
    """Sanitize a string for safe HTML display."""
    if value is None:
        return ""

    # HTML entity encoding for special characters
    value = (
        value.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#x27;")
    )

    return value


def sanitize_for_log(value: str | None) -> str:
    """Sanitize a string for safe logging (prevent log injection).

    Note: This prevents log entry injection via newlines, but does NOT
    filter content itself. Use validate_team_name for content filtering.
    """
    if value is None:
        return ""

    # Remove newlines and carriage returns to prevent log injection
    value = value.replace("\n", " ").replace("\r", "")
    value = value.replace("\t", " ")

    # Remove other control characters
    value = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]", "", value)

    # Limit length for logs
    value = value[:500]

    return value


def validate_fixture_id(fixture_id: Any) -> ValidationResult:
    """Validate fixture ID."""
    if fixture_id is None:
        return ValidationResult(valid=False, error="fixture_id is required")

    try:
        fid = int(fixture_id)
    except (ValueError, TypeError):
        return ValidationResult(valid=False, error="fixture_id must be integer")

    if fid <= 0 or fid > 99999999:
        return ValidationResult(valid=False, error="fixture_id out of range")

    return ValidationResult(valid=True, sanitized=fid)


def validate_league_id(league_id: Any) -> ValidationResult:
    """Validate league ID."""
    if league_id is None:
        return ValidationResult(valid=False, error="league_id is required")

    try:
        lid = int(league_id)
    except (ValueError, TypeError):
        return ValidationResult(valid=False, error="league_id must be integer")

    if lid <= 0 or lid > 9999:
        return ValidationResult(valid=False, error="league_id out of range")

    return ValidationResult(valid=True, sanitized=lid)


def validate_market(market: str | None) -> ValidationResult:
    """Validate market name."""
    if market is None:
        return ValidationResult(valid=False, error="market is required")

    market_str = str(market).strip().lower()

    # Whitelist of valid markets
    valid_markets = {
        "h2h", "btts", "ou25", "ou15", "ou35",
        "ah", "ah_0", "ah_-1", "ah_+1",
        "cs", "ht_ft",
        "first_scorer", "anytime_scorer",
        "corners", "cards", "draw_no_bet",
    }

    if market_str not in valid_markets:
        return ValidationResult(valid=False, error=f"unknown market: {market}")

    return ValidationResult(valid=True, sanitized=market_str)


def validate_days_param(days: Any) -> ValidationResult:
    """Validate days parameter for queries."""
    if days is None:
        return ValidationResult(valid=False, error="days is required")

    if isinstance(days, str):
        if days.lower() == "all":
            return ValidationResult(valid=True, sanitized="all")
        try:
            days = int(days)
        except ValueError:
            return ValidationResult(valid=False, error="days must be integer or 'all'")

    try:
        di = int(days)
    except (ValueError, TypeError):
        return ValidationResult(valid=False, error="days must be integer")

    if di < 1 or di > 365:
        return ValidationResult(valid=False, error="days must be 1-365")

    return ValidationResult(valid=True, sanitized=di)


def validate_limit_param(limit: Any, max_limit: int = 1000) -> ValidationResult:
    """Validate limit parameter."""
    if limit is None:
        return ValidationResult(valid=True, sanitized=100)  # default

    try:
        lim = int(limit)
    except (ValueError, TypeError):
        return ValidationResult(valid=False, error="limit must be integer")

    if lim < 1 or lim > max_limit:
        return ValidationResult(valid=False, error=f"limit must be 1-{max_limit}")

    return ValidationResult(valid=True, sanitized=lim)


def validate_team_name(name: str | None) -> ValidationResult:
    """Validate and sanitize team name."""
    if name is None or not name.strip():
        return ValidationResult(valid=False, error="team name required")

    original = name.strip()

    # Check for suspicious patterns BEFORE sanitization
    suspicious = [
        r"<script",
        r"javascript:",
        r"onerror=",
        r"onload=",
        r"onclick=",
        r"onmouseover=",
    ]

    original_lower = original.lower()
    for pattern in suspicious:
        if re.search(pattern, original_lower, re.IGNORECASE):
            return ValidationResult(valid=False, error="suspicious content in team name")

    # Sanitize for display
    sanitized = sanitize_for_html(original)

    return ValidationResult(valid=True, sanitized=sanitized)


def validate_event_payload(payload: dict | None) -> ValidationResult:
    """Validate event payload structure."""
    if payload is None:
        return ValidationResult(valid=False, error="payload is required")

    if not isinstance(payload, dict):
        return ValidationResult(valid=False, error="payload must be dict")

    return ValidationResult(valid=True, sanitized=payload)
