"""Supabase client and shared conversion helpers."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from todai.database.config import (
    DEFAULT_SANDBOX_USER_ID,
    supabase_configured,
    supabase_service_role_key,
    supabase_url,
)
from todai.database.utils.tz import get_timezone

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover
    Client = Any  # type: ignore[misc, assignment]
    create_client = None  # type: ignore[assignment]


def _parse_uuid(value: str) -> str | None:
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError):
        return None


def resolve_db_user_id(user_id: str) -> str:
    parsed = _parse_uuid(user_id)
    if parsed:
        return parsed
    if user_id == "default":
        return DEFAULT_SANDBOX_USER_ID
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"todai-user:{user_id}"))


def get_supabase_client() -> Client:
    if not supabase_configured() or create_client is None:
        raise RuntimeError(
            "Supabase not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, "
            "and install the supabase package."
        )
    return create_client(supabase_url(), supabase_service_role_key())


def month_bounds(year_month: str) -> tuple[date, date]:
    y, m = int(year_month[:4]), int(year_month[5:7])
    if m == 12:
        nxt = date(y + 1, 1, 1)
    else:
        nxt = date(y, m + 1, 1)
    return date(y, m, 1), nxt - timedelta(days=1)


def local_naive_to_utc(iso_local: str, tz_name: str) -> datetime:
    s = iso_local.strip().replace("Z", "")
    if "T" in s:
        naive = datetime.fromisoformat(s[:19])
    else:
        naive = datetime.strptime(s[:10], "%Y-%m-%d")
    z = get_timezone(tz_name)
    if "T" in s:
        aware = naive.replace(tzinfo=z)
    else:
        aware = datetime.combine(naive.date(), time.min, tzinfo=z)
    return aware.astimezone(timezone.utc)


def utc_to_local_naive_str(dt_utc: datetime, tz_name: str) -> str:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    z = get_timezone(tz_name)
    return dt_utc.astimezone(z).strftime("%Y-%m-%dT%H:%M:%S")


def parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    if "T" in s:
        dt = datetime.fromisoformat(s[:26])
    else:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
