"""
src/utils/timezone.py

Timezone utilities for local time display.
Uses configurable timezone from settings.
"""
from datetime import datetime, timezone

from config.settings import settings


def to_local(dt: datetime | None) -> datetime:
    """Convert UTC datetime to configured local timezone.

    Args:
        dt: UTC datetime (aware or naive)

    Returns:
        datetime in configured local timezone
    """
    if dt is None:
        return datetime.now()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(settings.timezone))
    except ImportError:
        # Fallback for Python < 3.9
        import pytz
        return dt.astimezone(pytz.timezone(settings.timezone))


def format_local(dt: datetime | None, fmt: str = "%H:%M %b %d") -> str:
    """Format UTC datetime as local time string.

    Args:
        dt: UTC datetime
        fmt: strftime format string

    Returns:
        Formatted local time string
    """
    if dt is None:
        return "TBD"

    local = to_local(dt)
    return local.strftime(fmt)


def now_local() -> datetime:
    """Get current time in local timezone."""
    return to_local(datetime.now(timezone.utc))


def tz_name() -> str:
    """Get configured local timezone name."""
    return settings.timezone
