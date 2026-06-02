"""Timezone helpers — work on Windows without IANA DB unless tzdata is installed."""

from __future__ import annotations

from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_timezone(tz_name: str) -> tzinfo:
    name = (tz_name or "UTC").strip() or "UTC"
    if name.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ModuleNotFoundError, Exception):
        return timezone.utc
