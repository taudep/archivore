"""Small time helpers shared by commands."""

from datetime import datetime, timedelta, timezone


def days_ago(days: int) -> datetime:
    """Return the UTC instant ``days`` days before now."""
    return datetime.now(timezone.utc) - timedelta(days=days)
