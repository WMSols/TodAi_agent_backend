"""
preview_range.py — resolve calendar time window from user message + DATE_ANCHOR

Agent horizon: 14 calendar days inclusive from server today (read/write/prefetch).
Default (vague / upcoming): full agent window. Explicit day/week/month phrases are
resolved then clamped to the window.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from todai.database.storage import parse_server_date

AGENT_WINDOW_DAYS = 14

_MONTH_NAME_TO_NUM = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_NAME_PATTERN = "|".join(sorted(_MONTH_NAME_TO_NUM.keys(), key=len, reverse=True))

_THIS_MONTH = re.compile(r"\bthis\s+month\b", re.I)
_NEXT_MONTH = re.compile(r"\bnext\s+month\b", re.I)
_LAST_MONTH = re.compile(r"\b(?:last|previous)\s+month\b", re.I)
_MONTH_OF = re.compile(rf"\bmonth\s+of\s+({_MONTH_NAME_PATTERN})\b", re.I)
_IN_MONTH = re.compile(rf"\b(?:in|for)\s+({_MONTH_NAME_PATTERN})(?:\s+(\d{{4}}))?\b", re.I)
_NAMED_MONTH = re.compile(rf"\b({_MONTH_NAME_PATTERN})(?:\s+(\d{{4}}))?\b", re.I)

_WEEK_PHRASES = re.compile(r"\bthis\s+week\b|\bnext\s+week\b", re.I)
_UPCOMING_PHRASES = re.compile(
    r"\bupcoming\b|\bwhat'?s\s+on\b|\bshow\s+(?:me\s+)?(?:my\s+)?(?:the\s+)?schedule\b|"
    r"\bpreview\b|\bmy\s+week\b|\bcoming\s+up\b",
    re.I,
)
_DAY_FOCUS = re.compile(
    r"\b(?:for|on)\s+(?:tomorrow|today)\b|"
    r"\b(?:tomorrow|today)(?:'s)?\s+schedule\b|"
    r"\bschedule\s+(?:for|on)\s+",
    re.I,
)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_FIRST_WEEK_OF_MONTH = re.compile(rf"\bfirst\s+week\s+of\s+({_MONTH_NAME_PATTERN})\b", re.I)


@dataclass(frozen=True)
class PreviewRange:
    date_from: str
    date_to: str
    label: str
    granularity: str  # day | week | month
    explicit: bool
    fill_empty_days: bool = True
    show_free_banners: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "from": self.date_from,
            "to": self.date_to,
            "label": self.label,
            "granularity": self.granularity,
            "explicit": self.explicit,
        }


def _day_label(d: date) -> str:
    return d.strftime("%A, %d %B %Y")


def agent_window_bounds(today: date) -> tuple[date, date]:
    """Inclusive agent horizon: today through today + (AGENT_WINDOW_DAYS - 1)."""
    return today, today + timedelta(days=AGENT_WINDOW_DAYS - 1)


def agent_window_as_dict(today: date) -> dict[str, str | int]:
    start, end = agent_window_bounds(today)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": AGENT_WINDOW_DAYS,
        "label": f"{start.strftime('%d %B')} – {end.strftime('%d %B %Y')}",
    }


def default_agent_window_range(today: date) -> PreviewRange:
    start, end = agent_window_bounds(today)
    return PreviewRange(
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        label=f"Next {AGENT_WINDOW_DAYS} days · {start.strftime('%d %b')} – {end.strftime('%d %b %Y')}",
        granularity="week",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )


def user_request_outside_agent_window(
    message: str,
    today: date,
    anchor: dict[str, Any] | None = None,
) -> bool:
    """True when the user asked for dates entirely outside, or beyond, the 14-day window."""
    win_start, win_end = agent_window_bounds(today)
    text = message or ""

    for match in _ISO_DATE.finditer(text):
        try:
            d = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if d < win_start or d > win_end:
            return True

    bounds = resolve_calendar_month_bounds(text, today, anchor)
    if bounds:
        req_start, req_end = bounds
        if req_end < win_start or req_start > win_end:
            return True
        if req_start < win_start or req_end > win_end:
            return True

    return False


def clamp_preview_range(
    scope: PreviewRange,
    today: date,
) -> PreviewRange:
    """Clip any resolved scope to the agent window (never load more than 14 days)."""
    win_start, win_end = agent_window_bounds(today)
    try:
        req_start = date.fromisoformat(scope.date_from[:10])
        req_end = date.fromisoformat(scope.date_to[:10])
    except ValueError:
        return default_agent_window_range(today)

    clamped_start = max(req_start, win_start)
    clamped_end = min(req_end, win_end)
    if clamped_end < clamped_start:
        return default_agent_window_range(today)

    if clamped_start == req_start and clamped_end == req_end:
        return scope

    label = scope.label
    if clamped_start != req_start or clamped_end != req_end:
        label = f"{clamped_start.strftime('%d %b')} – {clamped_end.strftime('%d %b %Y')} (within {AGENT_WINDOW_DAYS}-day window)"

    return PreviewRange(
        date_from=clamped_start.isoformat(),
        date_to=clamped_end.isoformat(),
        label=label,
        granularity=scope.granularity if scope.granularity != "month" else "week",
        explicit=scope.explicit,
        fill_empty_days=scope.fill_empty_days,
        show_free_banners=scope.show_free_banners,
    )


def _week_default(today: date) -> PreviewRange:
    return default_agent_window_range(today)


def _single_day(d: date) -> PreviewRange:
    return PreviewRange(
        date_from=d.isoformat(),
        date_to=d.isoformat(),
        label=_day_label(d),
        granularity="day",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_n = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_n)


def _shift_calendar_month(today: date, delta_months: int) -> tuple[date, date]:
    """First and last day of calendar month relative to today's month."""
    m0 = (today.year * 12 + (today.month - 1)) + delta_months
    year = m0 // 12
    month = m0 % 12 + 1
    return _month_bounds(year, month)


def _preview_month(first: date, last: date, *, explicit: bool) -> PreviewRange:
    return PreviewRange(
        date_from=first.isoformat(),
        date_to=last.isoformat(),
        label=first.strftime("%B %Y"),
        granularity="month",
        explicit=explicit,
        fill_empty_days=False,
        show_free_banners=False,
    )


def _month_num_from_name(name: str) -> int | None:
    return _MONTH_NAME_TO_NUM.get((name or "").lower().strip())


def _year_for_month(month_num: int, today: date, year_hint: int | None) -> int:
    if year_hint is not None:
        return year_hint
    return today.year


def resolve_calendar_month_bounds(
    message: str,
    today: date,
    anchor: dict[str, Any] | None = None,
) -> tuple[date, date] | None:
    """
    Calendar month (first day, last day) implied by the message — same rules as month preview scope.
    Used to anchor weekday names (e.g. \"monday in june\") without duplicating parsing logic.
    """
    m = (message or "").lower().strip()
    anchor = anchor or {}

    if _THIS_MONTH.search(m) and anchor.get("month"):
        month = anchor["month"]
        try:
            first = date.fromisoformat(str(month["first_day"]["iso"]))
            last = date.fromisoformat(str(month["last_day"]["iso"]))
            return first, last
        except (TypeError, ValueError):
            pass

    if _NEXT_MONTH.search(m):
        return _shift_calendar_month(today, 1)

    if _LAST_MONTH.search(m):
        return _shift_calendar_month(today, -1)

    match = _MONTH_OF.search(message or "")
    if match:
        num = _month_num_from_name(match.group(1))
        if num:
            return _month_bounds(_year_for_month(num, today, None), num)

    match = _IN_MONTH.search(message or "")
    if match:
        num = _month_num_from_name(match.group(1))
        if num:
            hint = int(match.group(2)) if match.group(2) else None
            year = _year_for_month(num, today, hint)
            return _month_bounds(year, num)

    match = _NAMED_MONTH.search(message or "")
    if match and not _NEXT_MONTH.search(m) and not _LAST_MONTH.search(m):
        num = _month_num_from_name(match.group(1))
        if num:
            hint = int(match.group(2)) if match.group(2) else None
            year = _year_for_month(num, today, hint)
            return _month_bounds(year, num)

    return None


def resolve_weekday_lookup_bounds(
    message: str,
    today: date,
    anchor: dict[str, Any] | None = None,
) -> tuple[date, date] | None:
    """
    Date window for resolving bare weekday words — intersected with the 14-day agent window.
    """
    win_start, win_end = agent_window_bounds(today)
    raw: tuple[date, date] | None = None
    match = _FIRST_WEEK_OF_MONTH.search(message or "")
    if match:
        num = _month_num_from_name(match.group(1))
        if num:
            year = _year_for_month(num, today, None)
            first, last = _month_bounds(year, num)
            raw = (first, min(first + timedelta(days=6), last))
    if raw is None:
        raw = resolve_calendar_month_bounds(message, today, anchor)
    if raw is None:
        return None
    start = max(raw[0], win_start)
    end = min(raw[1], win_end)
    if end < start:
        return None
    return start, end


def message_has_month_phrase(message: str) -> bool:
    """True when the user named a calendar month (not just a weekday)."""
    m = (message or "").lower()
    if _NEXT_MONTH.search(m) or _LAST_MONTH.search(m) or _MONTH_OF.search(m):
        return True
    if _THIS_MONTH.search(m):
        return True
    if _IN_MONTH.search(m):
        return True
    if _NAMED_MONTH.search(m):
        return True
    return False


def _resolve_calendar_month_phrase(message: str, today: date, anchor: dict[str, Any]) -> PreviewRange | None:
    bounds = resolve_calendar_month_bounds(message, today, anchor)
    if bounds:
        first, last = bounds
        return _preview_month(first, last, explicit=True)
    return None


def _next_calendar_week(today: date) -> PreviewRange:
    days_until_next_monday = (7 - today.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    start = today + timedelta(days=days_until_next_monday)
    end = start + timedelta(days=6)
    return PreviewRange(
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        label=f"{start.strftime('%d %B')} – {end.strftime('%d %B %Y')}",
        granularity="week",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )


def _this_calendar_week(today: date) -> PreviewRange:
    end = today + timedelta(days=(6 - today.weekday()))
    return PreviewRange(
        date_from=today.isoformat(),
        date_to=end.isoformat(),
        label=f"{today.strftime('%d %B')} – {end.strftime('%d %B %Y')}",
        granularity="week",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )


def resolve_time_scope(
    message: str,
    date_anchor: dict[str, Any] | None,
    *,
    full_index: dict[str, Any] | None = None,
) -> PreviewRange:
    """
    Resolve prefetch/preview window. Day and week rules unchanged; adds any calendar month.
    """
    today = parse_server_date(full_index or date_anchor)
    anchor = date_anchor or {}
    m = (message or "").lower().strip()

    iso_match = _ISO_DATE.search(message or "")
    scope: PreviewRange | None = None

    if iso_match:
        try:
            scope = _single_day(date.fromisoformat(iso_match.group(1)))
        except ValueError:
            pass

    if scope is None and re.search(r"\btomorrow\b", m):
        scope = _single_day(today + timedelta(days=1))

    if scope is None and re.search(r"\btoday\b", m) and not _WEEK_PHRASES.search(m) and not message_has_month_phrase(message):
        if _DAY_FOCUS.search(m) or re.search(r"\bschedule\b", m):
            scope = _single_day(today)

    if scope is None and re.search(r"\bnext\s+week\b", m):
        scope = _next_calendar_week(today)

    if scope is None and re.search(r"\bthis\s+week\b", m):
        scope = _this_calendar_week(today)

    if scope is None:
        month_scope = _resolve_calendar_month_phrase(message, today, anchor)
        if month_scope:
            scope = month_scope

    if scope is None:
        mentioned = anchor.get("mentioned_weekdays") or {}
        if len(mentioned) == 1 and not _WEEK_PHRASES.search(m) and not message_has_month_phrase(message):
            if (
                _DAY_FOCUS.search(m)
                or re.search(r"\b(?:on|for)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", m)
                or not re.search(r"\bupcoming\b", m)
            ):
                try:
                    d = date.fromisoformat(next(iter(mentioned.values())))
                    scope = _single_day(d)
                except ValueError:
                    pass

    if scope is None:
        scope = _week_default(today)

    return clamp_preview_range(scope, today)


def resolve_preview_range(
    message: str,
    date_anchor: dict[str, Any] | None,
    *,
    full_index: dict[str, Any] | None = None,
) -> PreviewRange:
    """Alias used by schedule_preview intent."""
    return resolve_time_scope(message, date_anchor, full_index=full_index)


def apply_preview_range_to_tools(
    tool_calls: list[dict[str, Any]],
    preview: PreviewRange,
) -> list[dict[str, Any]]:
    """Set get_schedule_range arguments to the resolved window."""
    want = {"from": preview.date_from, "to": preview.date_to}
    out: list[dict[str, Any]] = []
    replaced = False
    for call in tool_calls:
        if call.get("tool") == "get_schedule_range":
            if not replaced:
                out.append({"tool": "get_schedule_range", "arguments": want})
                replaced = True
            continue
        out.append(call)
    if not replaced:
        out.insert(0, {"tool": "get_schedule_range", "arguments": want})
    return out
