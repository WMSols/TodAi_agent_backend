"""
preview_range.py — preview window, read kind, router time_scope from user message + DATE_ANCHOR

Agent horizon: 14 calendar days inclusive from server today (read/write/prefetch).
Default (vague / upcoming): full agent window. Explicit day/week/month phrases are
resolved then clamped to the window.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, replace
from enum import Enum
from datetime import date, timedelta
from typing import Any

from todai.database.storage import parse_server_date

# --- Preview read kind ---


class PreviewReadKind(str, Enum):
    SCHEDULE = "schedule"
    FREE_DAYS = "free_days"
    FREE_TIME = "free_time"


_FREE_DAYS = re.compile(
    r"\bfree\s+days?\b"
    r"|\bdays?\s+without\s+(?:a\s+)?schedule\b"
    r"|\bwithout\s+(?:a\s+)?schedule\b"
    r"|\bno\s+schedule\b"
    r"|\bempty\s+days?\b"
    r"|\bdays?\s+(?:with\s+)?no\s+(?:events?|plans?)\b"
    r"|\bwhich\s+days?\s+(?:are\s+)?free\b"
    r"|\bany\s+free\s+days?\b",
    re.I,
)

_FREE_TIME = re.compile(
    r"\bfree\s+time\b"
    r"|\bfree\s+slots?\b"
    r"|\bavailable\s+(?:time|slots?)\b"
    r"|\btime\s+slots?\b"
    r"|\bwhen\s+(?:am\s+)?i\s+free\b"
    r"|\bgaps?\s+(?:in\s+)?(?:my\s+)?(?:day|schedule)\b"
    r"|\bopen\s+(?:time|slots?)\b",
    re.I,
)


def classify_preview_read(message: str) -> PreviewReadKind:
    """Free-day questions win over free-time when both could match."""
    m = (message or "").strip()
    if not m:
        return PreviewReadKind.SCHEDULE
    if _FREE_DAYS.search(m):
        return PreviewReadKind.FREE_DAYS
    if _FREE_TIME.search(m):
        return PreviewReadKind.FREE_TIME
    return PreviewReadKind.SCHEDULE

# --- Weekday pick ---
def pick_nearest_weekday_option(
    options: list[dict[str, str]],
    today: date,
) -> str | None:
    """Nearest calendar date on or after today; else earliest in the list."""
    isos: list[str] = []
    for opt in options:
        raw = (opt.get("iso") or "")[:10]
        if len(raw) == 10:
            isos.append(raw)
    if not isos:
        return None
    isos.sort()
    for iso in isos:
        try:
            if date.fromisoformat(iso) >= today:
                return iso
        except ValueError:
            continue
    return isos[0]


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

_WEEK_PHRASES = re.compile(
    r"\bthis\s+week\b|\bnext\s+week\b|"
    r"\bnext\s+all\s+week\b|\ball\s+(?:of\s+)?next\s+week\b|\bcoming\s+week\b",
    re.I,
)
_NEXT_WEEK_PHRASES = re.compile(
    r"\bnext\s+week\b|\bnext\s+all\s+week\b|\ball\s+(?:of\s+)?next\s+week\b|\bcoming\s+week\b",
    re.I,
)
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
class PreviewTarget:
    """One calendar day the user asked about (discrete preview)."""
    weekday: str
    iso: str
    label: str


@dataclass(frozen=True)
class PreviewRange:
    date_from: str
    date_to: str
    label: str
    granularity: str  # day | week | month | discrete_days
    explicit: bool
    fill_empty_days: bool = True
    show_free_banners: bool = False
    scope_mode: str = "range"  # range | discrete_days
    target_days: tuple[str, ...] | None = None

    def as_dict(self) -> dict[str, str | bool | list[str]]:
        out: dict[str, str | bool | list[str]] = {
            "from": self.date_from,
            "to": self.date_to,
            "label": self.label,
            "granularity": self.granularity,
            "explicit": self.explicit,
            "fill_empty_days": self.fill_empty_days,
            "show_free_banners": self.show_free_banners,
            "scope_mode": self.scope_mode,
        }
        if self.target_days:
            out["target_days"] = list(self.target_days)
        return out


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

    clamped_targets = scope.target_days
    if scope.target_days:
        win_s, win_e = clamped_start.isoformat(), clamped_end.isoformat()
        kept = tuple(d for d in scope.target_days if win_s <= d <= win_e)
        clamped_targets = kept if kept else None
    return PreviewRange(
        date_from=clamped_start.isoformat(),
        date_to=clamped_end.isoformat(),
        label=label,
        granularity=scope.granularity if scope.granularity != "month" else "week",
        explicit=scope.explicit,
        fill_empty_days=scope.fill_empty_days,
        show_free_banners=scope.show_free_banners,
        scope_mode=scope.scope_mode,
        target_days=clamped_targets,
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

    if scope is None and _NEXT_WEEK_PHRASES.search(m):
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
        candidates = anchor.get("weekday_candidates") or {}
        asks_weekday = bool(
            _DAY_FOCUS.search(m)
            or re.search(r"\b(?:on|for)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", m)
            or re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", m)
        )
        if (
            asks_weekday
            and len(candidates) == 1
            and not _WEEK_PHRASES.search(m)
            and not message_has_month_phrase(message)
        ):
            opts = next(iter(candidates.values()))
            iso = pick_nearest_weekday_option(opts, today)
            if iso:
                try:
                    scope = _single_day(date.fromisoformat(iso[:10]))
                except ValueError:
                    pass

    if scope is None:
        scope = _week_default(today)

    return clamp_preview_range(scope, today)


_RANGE_TOOLS = frozenset(
    {"get_schedule_range", "get_free_time", "get_days_without_schedule"}
)


def apply_preview_range_to_tools(
    tool_calls: list[dict[str, Any]],
    preview: PreviewRange,
) -> list[dict[str, Any]]:
    """Align all range read tools to the resolved preview window."""
    want: dict[str, Any] = {"from": preview.date_from, "to": preview.date_to}
    if preview.scope_mode == "discrete_days" and preview.target_days:
        want["target_days"] = list(preview.target_days)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in tool_calls:
        tool = str(call.get("tool") or "")
        if tool in _RANGE_TOOLS:
            if tool not in seen:
                out.append({"tool": tool, "arguments": dict(want)})
                seen.add(tool)
            continue
        out.append(call)
    return out


# --- Router time_scope ---

from todai.agent.routing.date_anchor import (
    day_target_isos,
    message_has_coming_named_weekday,
    message_has_named_weekday_target,
    message_has_next_named_weekday,
    message_has_this_named_weekday,
    message_has_whole_week_phrase,
    multi_resolved_weekday_isos,
    single_day_iso_from_anchor,
)

_WEEKDAY_IN_MESSAGE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b",
    re.I,
)
_BOTH_WORDS = re.compile(r"\b(?:both|all\s+of\s+them)\b", re.I)
_MULTI_WEEKDAY_PHRASE = re.compile(
    r"\bthis\s+\w+day\s+and\s+(?:next\s+|coming\s+)?\w+day\b"
    r"|\band\s+(?:next\s+|coming\s+)\w+day\b"
    r"|\b\w+day\s+and\s+(?:next\s+|coming\s+)?\w+day\b",
    re.I,
)

# Router outputs one of these (no ISO dates in tools).
SCOPE_DEFAULT = "default"
SCOPE_TODAY = "today"
SCOPE_TOMORROW = "tomorrow"
SCOPE_THIS_WEEK = "this_week"
SCOPE_NEXT_WEEK = "next_week"
SCOPE_SINGLE_DAY = "single_day"
SCOPE_DISCRETE_DAYS = "discrete_days"
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
    "discrete_days": SCOPE_DISCRETE_DAYS,
    "discrete days": SCOPE_DISCRETE_DAYS,
    "multi_day": SCOPE_DISCRETE_DAYS,
    "multi day": SCOPE_DISCRETE_DAYS,
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
    if keyword == SCOPE_DISCRETE_DAYS:
        return None
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
    """User wants more than one date (candidates or multiple resolved mentioned_weekdays)."""
    if len(multi_resolved_weekday_isos(date_anchor)) >= 2:
        msg = message or ""
        if _BOTH_WORDS.search(msg) or _MULTI_WEEKDAY_PHRASE.search(msg):
            return True
        if message_has_named_weekday_target(msg) and _WEEKDAY_IN_MESSAGE.search(msg):
            return True
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
    return _scope_from_iso_span(isos, today)


def scope_from_mentioned_weekdays(
    date_anchor: dict[str, Any] | None,
    today: date,
) -> PreviewRange | None:
    """Span min–max when mentioned_weekdays resolved 2+ distinct days (e.g. coming sunday + next thursday)."""
    isos = multi_resolved_weekday_isos(date_anchor)
    if len(isos) < 2:
        return None
    return _scope_from_iso_span(isos, today)


def _weekday_keys_in_message(message: str) -> set[str]:
    """Canonical weekday names (lowercase) mentioned in the message."""
    keys: set[str] = set()
    for match in _WEEKDAY_IN_MESSAGE.finditer(message or ""):
        raw = match.group(0).lower()
        idx = {
            "monday": "monday", "mon": "monday",
            "tuesday": "tuesday", "tue": "tuesday", "tues": "tuesday",
            "wednesday": "wednesday", "wed": "wednesday",
            "thursday": "thursday", "thu": "thursday", "thur": "thursday", "thurs": "thursday",
            "friday": "friday", "fri": "friday",
            "saturday": "saturday", "sat": "saturday",
            "sunday": "sunday", "sun": "sunday",
        }.get(raw)
        if idx:
            keys.add(idx)
    return keys


def message_requests_discrete_day_preview(
    message: str,
    date_anchor: dict[str, Any] | None,
) -> bool:
    """User named 2+ specific weekdays (same or different weeks) — not a full-week ask."""
    if message_has_whole_week_phrase(message):
        return False
    day_targets = (date_anchor or {}).get("day_targets") or []
    if len(day_targets) >= 2 and len(day_target_isos(date_anchor)) >= 2:
        return True
    if message_implies_multi_weekday_scope(message, date_anchor):
        return True
    if len(multi_resolved_weekday_isos(date_anchor)) >= 2:
        return True
    msg = message or ""
    if message_has_named_weekday_target(msg) and _MULTI_WEEKDAY_PHRASE.search(msg):
        return True
    asked = _weekday_keys_in_message(msg)
    if len(asked) >= 2:
        if _MULTI_WEEKDAY_PHRASE.search(msg) or _BOTH_WORDS.search(msg):
            return True
        if re.search(r"\band\b", msg, re.I):
            return True
    return False


def build_discrete_preview_targets(
    message: str,
    date_anchor: dict[str, Any] | None,
    today: date,
) -> list[PreviewTarget]:
    """
    One ISO per weekday the user asked about (resolved or nearest candidate).
    Does not widen to intermediate calendar days between targets.
    """
    if not message_requests_discrete_day_preview(message, date_anchor):
        return []
    anchor = date_anchor or {}
    day_targets = anchor.get("day_targets") or []
    if len(day_targets) >= 2 and len(day_target_isos(date_anchor)) >= 2:
        out: list[PreviewTarget] = []
        seen: set[str] = set()
        for t in day_targets:
            iso = str(t.get("iso") or "")[:10]
            if len(iso) != 10 or iso in seen:
                continue
            seen.add(iso)
            out.append(
                PreviewTarget(
                    str(t.get("weekday") or ""),
                    iso,
                    str(t.get("label") or iso),
                )
            )
        if len(out) >= 2:
            return out

    mentioned = anchor.get("mentioned_weekdays") or {}
    candidates = anchor.get("weekday_candidates") or {}
    asked = _weekday_keys_in_message(message)
    targets: list[PreviewTarget] = []

    for wd in sorted(asked):
        iso = str(mentioned.get(wd) or "")[:10]
        if len(iso) == 10:
            try:
                d = date.fromisoformat(iso)
                targets.append(PreviewTarget(wd, iso, d.strftime("%A, %d %B %Y")))
            except ValueError:
                pass
            continue
        opts = candidates.get(wd)
        if isinstance(opts, list) and opts:
            picked = pick_nearest_weekday_option(opts, today)
            if picked:
                try:
                    d = date.fromisoformat(picked[:10])
                    targets.append(PreviewTarget(wd, picked[:10], d.strftime("%A, %d %B %Y")))
                except ValueError:
                    pass

    seen: set[str] = set()
    unique: list[PreviewTarget] = []
    for t in sorted(targets, key=lambda x: x.iso):
        if t.iso in seen:
            continue
        seen.add(t.iso)
        unique.append(t)
    return unique


def preview_range_from_discrete_targets(
    targets: list[PreviewTarget],
    today: date,
) -> PreviewRange | None:
    if len(targets) < 2:
        return None
    isos = [t.iso for t in targets]
    try:
        d0 = date.fromisoformat(isos[0])
        d1 = date.fromisoformat(isos[-1])
    except ValueError:
        return None
    if len(targets) == 2:
        label = f"{targets[0].label} · {targets[1].label}"
    else:
        label = f"{d0.strftime('%A %d %b')} – {d1.strftime('%A %d %b %Y')} ({len(targets)} days)"
    scope = PreviewRange(
        date_from=d0.isoformat(),
        date_to=d1.isoformat(),
        label=label,
        granularity="discrete_days",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
        scope_mode="discrete_days",
        target_days=tuple(isos),
    )
    return clamp_preview_range(scope, today)


def _scope_from_iso_span(isos: list[str], today: date) -> PreviewRange | None:
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
    if message_requests_discrete_day_preview(message, date_anchor):
        return False
    if message_implies_multi_weekday_scope(message, date_anchor):
        return False
    day_targets = (date_anchor or {}).get("day_targets") or []
    if len(day_targets) >= 2:
        return False
    if len(multi_resolved_weekday_isos(date_anchor)) >= 2:
        return False
    if message_has_whole_week_phrase(message):
        return False
    if (
        message_has_next_named_weekday(message)
        or message_has_coming_named_weekday(message)
        or message_has_this_named_weekday(message)
    ):
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
        multi = scope_from_mentioned_weekdays(date_anchor, today)
        if multi:
            return multi
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


def align_router_time_scope(
    message: str,
    date_anchor: dict[str, Any] | None,
    route: str,
    time_scope: str,
) -> str:
    """Align router time_scope with code-resolved day targets (calendar routes)."""
    scope = normalize_time_scope(time_scope)
    if route not in ("schedule_preview", "schedule_delete", "schedule_write"):
        return scope
    if message_requests_discrete_day_preview(message, date_anchor):
        return SCOPE_DISCRETE_DAYS
    day_targets = (date_anchor or {}).get("day_targets") or []
    if len(day_targets) == 1:
        return SCOPE_SINGLE_DAY
    if len(multi_resolved_weekday_isos(date_anchor)) == 1 and (
        message_has_named_weekday_target(message) or single_day_iso_from_anchor(date_anchor)
    ):
        return SCOPE_SINGLE_DAY
    if route == "schedule_preview":
        kind = classify_preview_read(message)
        if kind == PreviewReadKind.FREE_DAYS:
            return SCOPE_FREE_DAYS
        if kind == PreviewReadKind.FREE_TIME:
            return SCOPE_FREE_TIME
    return scope


def infer_time_scope_from_message(
    message: str,
    date_anchor: dict[str, Any] | None = None,
) -> str:
    """Heuristic for mock router / tests."""
    if date_anchor and message_requests_discrete_day_preview(message, date_anchor):
        return SCOPE_DISCRETE_DAYS
    msg = message or ""
    if (
        len(_weekday_keys_in_message(msg)) >= 2
        and (_MULTI_WEEKDAY_PHRASE.search(msg) or _BOTH_WORDS.search(msg))
        and not message_has_whole_week_phrase(message)
    ):
        return SCOPE_DISCRETE_DAYS
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
    if message_has_named_weekday_target(message):
        return SCOPE_SINGLE_DAY
    if _WEEKDAY_IN_MESSAGE.search(message or "") and not message_has_whole_week_phrase(message):
        return SCOPE_SINGLE_DAY
    if "tomorrow" in m:
        return SCOPE_TOMORROW
    if "today" in m:
        return SCOPE_TODAY
    kind = classify_preview_read(message)
    if kind == PreviewReadKind.FREE_DAYS:
        return SCOPE_FREE_DAYS
    if kind == PreviewReadKind.FREE_TIME:
        return SCOPE_FREE_TIME
    return SCOPE_DEFAULT


def resolve_preview_range(
    message: str,
    date_anchor: dict[str, Any] | None,
    *,
    full_index: dict[str, Any] | None = None,
    time_scope: str | None = None,
) -> PreviewRange:
    """Alias used by schedule_preview intent."""
    return resolve_preview_range_for_turn(
        time_scope=time_scope,
        message=message,
        date_anchor=date_anchor,
        full_index=full_index,
        route="schedule_preview",
    )

