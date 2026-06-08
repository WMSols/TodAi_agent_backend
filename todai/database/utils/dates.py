"""Server 'today' and timezone helpers for calendar anchoring."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any
from todai.database.utils.tz import get_timezone

_TODAY_QUESTION = re.compile(
    r"(?i)\b(?:"
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:day|date)(?:\s+today)?|"
    r"what\s+day\s+is\s+(?:it|today)|"
    r"what\s+is\s+(?:the\s+)?current\s+(?:day|date)|"
    r"current\s+(?:day|date)|"
    r"today'?s\s+(?:day|date)"
    r")\b"
)


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


def build_today_info(
    *,
    storage_index: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Authoritative server today for LLM prompts (iso + weekday + label)."""
    now_utc = ""
    if storage_index is not None:
        today = parse_server_date(storage_index)
        raw = (storage_index.get("server_datetime_utc") or "")[:16]
        now_utc = str(raw) if raw else ""
    else:
        now = server_now(profile)
        today = now.date()
        now_utc = now.isoformat(timespec="minutes")[:16]
    return {
        "iso": today.isoformat(),
        "weekday": today.strftime("%A"),
        "label": today.strftime("%A, %d %B %Y"),
        "now_utc": now_utc,
    }


def is_today_question(message: str) -> bool:
    return bool(_TODAY_QUESTION.search((message or "").strip()))


def format_today_reply(today_info: dict[str, str]) -> str:
    label = (today_info.get("label") or "").strip()
    iso = (today_info.get("iso") or "").strip()
    if label:
        return f"Today is **{label}**."
    if iso:
        return f"Today's date is **{iso}**."
    return "I don't have today's date from the server right now."
