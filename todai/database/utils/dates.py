"""Server 'today' and timezone helpers for calendar anchoring."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any
from todai.database.utils.tz import get_timezone


def resolve_user_timezone(profile: dict[str, Any] | None = None) -> str:
    """IANA timezone for calendar 'today' (env TODAI_TIMEZONE overrides profile)."""
    env_tz = os.environ.get("TODAI_TIMEZONE", "").strip()
    if env_tz:
        return env_tz
    if profile:
        tz = (profile.get("timezone") or "").strip()
        if tz:
            return tz
    return "UTC"


def server_now(profile: dict[str, Any] | None = None) -> datetime:
    tz_name = resolve_user_timezone(profile)
    return datetime.now(get_timezone(tz_name))


def server_date_fields(profile: dict[str, Any] | None = None) -> tuple[str, str]:
    now = server_now(profile)
    return now.date().isoformat(), now.isoformat(timespec="minutes")


def parse_server_date(storage_index: dict[str, Any] | None) -> date:
    raw = (storage_index or {}).get("server_date_utc") or ""
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    profile = (storage_index or {}).get("profile")
    if isinstance(profile, dict):
        return server_now(profile).date()
    return server_now().date()
