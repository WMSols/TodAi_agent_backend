"""
time_scope.py — router time_scope keywords → PreviewRange (server-side dates)
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from todai.agent.routing.date_anchor import (
    message_has_next_named_weekday,
    message_has_whole_week_phrase,
    single_day_iso_from_anchor,
)

_WEEKDAY_IN_MESSAGE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b",
    re.I,
)
_BOTH_WORDS = re.compile(r"\b(?:both|all\s+of\s+them)\b", re.I)
_MULTI_WEEKDAY_PHRASE = re.compile(
    r"\bthis\s+\w+day\s+and\s+next\s+\w+day\b|\band\s+next\s+\w+day\b|\b\w+day\s+and\s+\w+day\b",
    re.I,
)

from todai.agent.routing.preview_range import (
    PreviewRange,
    _next_calendar_week,
    _single_day,
    _this_calendar_week,
    _week_default,
    clamp_preview_range,
    resolve_time_scope,
)
from todai.agent.routing.preview_read_kind import PreviewReadKind, classify_preview_read
from todai.database.storage import parse_server_date

# Router outputs one of these (no ISO dates in tools).
SCOPE_DEFAULT = "default"
SCOPE_TODAY = "today"
SCOPE_TOMORROW = "tomorrow"
SCOPE_THIS_WEEK = "this_week"
SCOPE_NEXT_WEEK = "next_week"
SCOPE_SINGLE_DAY = "single_day"
SCOPE_FREE_DAYS = "free_days"
SCOPE_FREE_TIME = "free_time"

_ALIASES: dict[str, str] = {
    "default": SCOPE_DEFAULT,
    "": SCOPE_DEFAULT,
    "today": SCOPE_TODAY,
    "tomorrow": SCOPE_TOMORROW,
    "this_week": SCOPE_THIS_WEEK,
    "this week": SCOPE_THIS_WEEK,
    "next_week": SCOPE_NEXT_WEEK,
    "next week": SCOPE_NEXT_WEEK,
    "all_next_week": SCOPE_NEXT_WEEK,
    "all next week": SCOPE_NEXT_WEEK,
    "next_all_week": SCOPE_NEXT_WEEK,
    "next all week": SCOPE_NEXT_WEEK,
    "coming_week": SCOPE_NEXT_WEEK,
    "single_day": SCOPE_SINGLE_DAY,
    "single day": SCOPE_SINGLE_DAY,
    "day": SCOPE_SINGLE_DAY,
    "free_days": SCOPE_FREE_DAYS,
    "free days": SCOPE_FREE_DAYS,
    "free_time": SCOPE_FREE_TIME,
    "free time": SCOPE_FREE_TIME,
}


def normalize_time_scope(raw: str | None) -> str:
    key = (raw or SCOPE_DEFAULT).strip().lower().replace("-", "_")
    return _ALIASES.get(key, SCOPE_DEFAULT)


_READ_TOOL_NAMES = frozenset(
    {
        "get_schedule_range",
        "get_free_time",
        "get_days_without_schedule",
        "get_active_goals",
        "analyze_progress",
    }
)


def normalize_router_tools(raw: Any) -> list[dict[str, Any]]:
    """Accept Groq variants: tool names as strings or {tool, arguments} objects."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name in _READ_TOOL_NAMES:
                out.append({"tool": name, "arguments": {}})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or item.get("tool_name") or "").strip()
        if not name and len(item) == 1:
            only = next(iter(item.values()))
            if isinstance(only, str):
                name = only.strip()
        if name in _READ_TOOL_NAMES:
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            out.append({"tool": name, "arguments": dict(args or {})})
    return out


def strip_router_tool_dates(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Router must not pin prefetch windows; server fills from/to after scope resolve."""
    if not tools:
        return []
    from todai.agent.routing.preview_range import _RANGE_TOOLS

    out: list[dict[str, Any]] = []
    for call in tools:
        if isinstance(call, str):
            call = {"tool": call.strip(), "arguments": {}}
        if not isinstance(call, dict):
            continue
        c = dict(call)
        tool = str(c.get("tool") or "").strip()
        args = dict(c.get("arguments") or {})
        for key in ("from", "to"):
            if key in c:
                c.pop(key, None)
        if tool in _RANGE_TOOLS:
            args = {}
        c["arguments"] = args
        c["tool"] = tool
        if tool:
            out.append(c)
    return out


def _scope_from_keyword(
    keyword: str,
    *,
    today: date,
    message: str,
    date_anchor: dict[str, Any] | None,
    full_index: dict[str, Any] | None,
) -> PreviewRange | None:
    anchor = date_anchor or {}
    if keyword == SCOPE_TODAY:
        return _single_day(today)
    if keyword == SCOPE_TOMORROW:
        return _single_day(today + timedelta(days=1))
    if keyword == SCOPE_THIS_WEEK:
        return _this_calendar_week(today)
    if keyword == SCOPE_NEXT_WEEK:
        return _next_calendar_week(today)
    if keyword in (SCOPE_FREE_DAYS, SCOPE_FREE_TIME):
        return clamp_preview_range(_week_default(today), today)
    if keyword == SCOPE_SINGLE_DAY:
        mentioned = anchor.get("mentioned_weekdays") or {}
        if len(mentioned) == 1:
            try:
                d = date.fromisoformat(next(iter(mentioned.values()))[:10])
                return _single_day(d)
            except ValueError:
                pass
        return None
    return None


def _weekday_candidate_isos(date_anchor: dict[str, Any] | None) -> list[str]:
    candidates = (date_anchor or {}).get("weekday_candidates") or {}
    isos: list[str] = []
    for opts in candidates.values():
        if not isinstance(opts, list):
            continue
        for opt in opts:
            raw = (opt.get("iso") or "")[:10]
            if len(raw) == 10:
                isos.append(raw)
    return sorted(set(isos))


def message_implies_multi_weekday_scope(
    message: str,
    date_anchor: dict[str, Any] | None,
) -> bool:
    """User wants more than one date from weekday_candidates (e.g. both Wednesdays)."""
    isos = _weekday_candidate_isos(date_anchor)
    if len(isos) < 2:
        return False
    msg = message or ""
    if _BOTH_WORDS.search(msg):
        return True
    if _MULTI_WEEKDAY_PHRASE.search(msg):
        return True
    return False


def scope_from_weekday_candidates(
    date_anchor: dict[str, Any] | None,
    today: date,
) -> PreviewRange | None:
    isos = _weekday_candidate_isos(date_anchor)
    if len(isos) < 2:
        return None
    try:
        d0 = date.fromisoformat(isos[0])
        d1 = date.fromisoformat(isos[-1])
    except ValueError:
        return None
    scope = PreviewRange(
        date_from=d0.isoformat(),
        date_to=d1.isoformat(),
        label=f"{d0.strftime('%A %d %b')} – {d1.strftime('%A %d %b %Y')}",
        granularity="week",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )
    return clamp_preview_range(scope, today)


def message_implies_single_day(
    message: str,
    date_anchor: dict[str, Any] | None,
) -> bool:
    """True when user targets one calendar day (not a full week phrase)."""
    if message_implies_multi_weekday_scope(message, date_anchor):
        return False
    if message_has_whole_week_phrase(message):
        return False
    if message_has_next_named_weekday(message):
        return True
    if single_day_iso_from_anchor(date_anchor):
        if _WEEKDAY_IN_MESSAGE.search(message or "") or re.search(
            r"\b(?:on|for)\s+(?:today|tomorrow)\b|\b\d{4}-\d{2}-\d{2}\b",
            message or "",
            re.I,
        ):
            return True
    return False


def refine_scope_for_message(
    scope: PreviewRange,
    *,
    message: str,
    date_anchor: dict[str, Any] | None,
    today: date,
) -> PreviewRange:
    """Prefer one-day scope when message names a day but router sent a week keyword."""
    if message_implies_multi_weekday_scope(message, date_anchor):
        multi = scope_from_weekday_candidates(date_anchor, today)
        if multi:
            return multi
    if not message_implies_single_day(message, date_anchor):
        return scope
    iso = single_day_iso_from_anchor(date_anchor)
    if not iso:
        return scope
    try:
        day_scope = _single_day(date.fromisoformat(iso[:10]))
    except ValueError:
        return scope
    return clamp_preview_range(day_scope, today)


def resolve_preview_range_for_turn(
    *,
    time_scope: str | None,
    message: str,
    date_anchor: dict[str, Any] | None,
    full_index: dict[str, Any] | None = None,
    route: str | None = None,
) -> PreviewRange:
    """Primary scope: router time_scope keyword; message/anchor rules fill gaps."""
    today = parse_server_date(full_index or date_anchor)
    keyword = normalize_time_scope(time_scope)
    scope: PreviewRange | None = None
    if keyword != SCOPE_DEFAULT:
        scope = _scope_from_keyword(
            keyword,
            today=today,
            message=message,
            date_anchor=date_anchor,
            full_index=full_index,
        )
    if scope is None:
        scope = resolve_time_scope(message, date_anchor, full_index=full_index)
    else:
        scope = clamp_preview_range(scope, today)
    scope = refine_scope_for_message(scope, message=message, date_anchor=date_anchor, today=today)
    if route == "schedule_preview" and classify_preview_read(message) == PreviewReadKind.FREE_DAYS:
        return replace(scope, show_free_banners=True)
    return scope


def infer_time_scope_from_message(message: str) -> str:
    """Heuristic for mock router / tests."""
    m = (message or "").lower()
    if message_has_whole_week_phrase(message) and not message_has_next_named_weekday(message):
        if any(
            p in m
            for p in (
                "next all week",
                "all of next week",
                "all next week",
                "next week",
                "coming week",
            )
        ):
            return SCOPE_NEXT_WEEK
        if "this week" in m:
            return SCOPE_THIS_WEEK
    if message_has_next_named_weekday(message):
        return SCOPE_SINGLE_DAY
    if _WEEKDAY_IN_MESSAGE.search(message or "") and not message_has_whole_week_phrase(message):
        return SCOPE_SINGLE_DAY
    if "tomorrow" in m:
        return SCOPE_TOMORROW
    if "today" in m:
        return SCOPE_TODAY
    from todai.agent.routing.preview_read_kind import classify_preview_read

    kind = classify_preview_read(message)
    if kind == PreviewReadKind.FREE_DAYS:
        return SCOPE_FREE_DAYS
    if kind == PreviewReadKind.FREE_TIME:
        return SCOPE_FREE_TIME
    return SCOPE_DEFAULT
