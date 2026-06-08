"""Parse user messages for goal_tasks_summary (day, progress-only, task name)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

Scope = Literal["week", "day", "progress_only", "task_match", "guidance"]

_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_PLAN_DAY_TASKS = re.compile(r"\btasks?\s+for\s+day\s*(\d{1,2})\b", re.I)
_PLAN_DAY_OF = re.compile(
    r"\bday\s*(\d{1,2})\s+(?:tasks|only|of\s+(?:the\s+)?(?:plan|week))\b",
    re.I,
)
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_DMY_SLASH = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b")
_DMY_TEXT = re.compile(
    r"\b(\d{1,2})\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.I,
)
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_PROGRESS = re.compile(
    r"\b(progress|how\s+(?:much|many)\s+(?:done|completed)|percent(?:age)?|done\s+so\s+far)\b",
    re.I,
)
_LIST_INTENT = re.compile(
    r"\b(?:what\b.*\b(?:tasks?|todos?)\b|"
    r"\b(?:tasks?|todos?|plan)\b|"
    r"\b(?:show|list|view|give)\b.*\b(?:tasks?|plan)\b|"
    r"\bshow\s+my\s+plan\b)",
    re.I,
)
_FULL_WEEK = re.compile(
    r"\b(?:all\s+)?(?:tasks?|plan|week|7[- ]?day)\b|"
    r"\b(?:this|my)\s+(?:goal|plan)\b.*\b(?:tasks?|progress)\b",
    re.I,
)
_GUIDANCE = re.compile(
    r"\b(?:how\s+(?:do|can|should)|help\s+(?:me\s+)?(?:with|on)|"
    r"guide\s+me|walk\s+me\s+through|explain|elaborate|"
    r"tips?\s+(?:for|on)|steps?\s+to|what\s+should\s+i\s+do|"
    r"advice|stuck\s+on|don't\s+know\s+how)\b",
    re.I,
)
_DELETE_VERB = re.compile(r"\b(?:delete|remove|clear|drop)\b", re.I)
_TASK_ORDINAL = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th|last)\s+task\b|"
    r"\btask\s*(#?\s*)(\d{1,2})\b",
    re.I,
)


@dataclass(frozen=True)
class TaskSummaryQuery:
    scope: Scope
    dates: tuple[str, ...] = ()
    day_label: str = ""
    matched_tasks: tuple[dict[str, Any], ...] = ()


def _dates_in_plan(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _weekday_dates(start: date, end: date, weekday: int) -> list[date]:
    return [d for d in _dates_in_plan(start, end) if d.weekday() == weekday]


def parse_day_dates_in_message(text: str, *, start: date, end: date) -> list[date]:
    """Resolve weekday / plan-day / explicit dates within the plan window."""
    day_dates = _parse_explicit_dates(text, start, end)
    if not day_dates:
        day_dates = _parse_weekday(text, start, end)
    if not day_dates:
        day_dates = _parse_plan_day_number(text, start, end)
    return day_dates


def resolve_task_ordinal(message: str, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Map 'first task', 'task 2', etc. to one task row (sorted by sort_order)."""
    if not tasks:
        return None
    ordered = sorted(tasks, key=lambda x: int(x.get("sort_order") or 0))
    text = (message or "").lower()
    if re.search(r"\blast\s+task\b", text):
        return ordered[-1]
    word_map = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3}
    for word, idx in word_map.items():
        if re.search(rf"\b{word}\s+task\b", text) and idx < len(ordered):
            return ordered[idx]
    m = _TASK_ORDINAL.search(message or "")
    if m and m.group(2):
        idx = int(m.group(2)) - 1
        if 0 <= idx < len(ordered):
            return ordered[idx]
    return None


def _parse_explicit_dates(text: str, start: date, end: date) -> list[date]:
    found: list[date] = []
    for m in _ISO_DATE.finditer(text):
        try:
            found.append(date.fromisoformat(m.group(1)))
        except ValueError:
            continue
    for m in _DMY_SLASH.finditer(text):
        try:
            found.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            continue
    for m in _DMY_TEXT.finditer(text):
        mon = _MONTHS.get(m.group(2).lower())
        if not mon:
            continue
        try:
            found.append(date(start.year, mon, int(m.group(1))))
        except ValueError:
            continue
    in_window = [d for d in found if start <= d <= end]
    return sorted(set(in_window))


def _parse_weekday(text: str, start: date, end: date) -> list[date]:
    low = text.lower()
    hits: list[date] = []
    for name, wd in _WEEKDAY_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            hits.extend(_weekday_dates(start, end, wd))
    return sorted(set(hits))


def _parse_plan_day_number(text: str, start: date, end: date) -> list[date]:
    plan_days = (end - start).days + 1
    found: list[date] = []
    for pat in (_PLAN_DAY_TASKS, _PLAN_DAY_OF):
        for m in pat.finditer(text):
            n = int(m.group(1))
            if 1 <= n <= plan_days:
                d = start + timedelta(days=n - 1)
                if start <= d <= end:
                    found.append(d)
    return sorted(set(found))


def _day_label_for(dates: list[date]) -> str:
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0].strftime("%A, %d %b")
    parts = [d.strftime("%a %d %b") for d in dates]
    return ", ".join(parts)


def _match_tasks_by_title(message: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msg = (message or "").strip().lower()
    if len(msg) < 8:
        return []
    matches: list[dict[str, Any]] = []
    for t in tasks:
        title = (t.get("title") or "").strip()
        if len(title) < 6:
            continue
        tl = title.lower()
        if tl in msg:
            matches.append(t)
            continue
        words = [w for w in re.findall(r"[a-z0-9]+", tl) if len(w) > 3]
        if len(words) >= 2 and sum(1 for w in words if w in msg) >= min(3, len(words)):
            matches.append(t)
    return matches


def parse_task_summary_query(
    message: str,
    *,
    start: date,
    end: date,
    tasks: list[dict[str, Any]],
) -> TaskSummaryQuery:
    """
    Decide how to narrow goal_tasks_summary replies.

    Priority: explicit day/date > task title match > progress-only > full week.
    """
    text = (message or "").strip()
    if not text:
        return TaskSummaryQuery(scope="week")

    if _GUIDANCE.search(text) and not _DELETE_VERB.search(text):
        day_dates = parse_day_dates_in_message(text, start=start, end=end)
        if day_dates:
            iso = tuple(d.isoformat() for d in day_dates)
            day_tasks = filter_tasks_by_dates(tasks, iso)
            return TaskSummaryQuery(
                scope="guidance",
                dates=iso,
                day_label=_day_label_for(day_dates),
                matched_tasks=tuple(day_tasks),
            )
        matched = _match_tasks_by_title(text, tasks)
        if matched:
            return TaskSummaryQuery(
                scope="guidance",
                matched_tasks=tuple(matched[:3]),
            )
        return TaskSummaryQuery(scope="guidance")

    day_dates = parse_day_dates_in_message(text, start=start, end=end)

    if day_dates:
        iso = tuple(d.isoformat() for d in day_dates)
        return TaskSummaryQuery(
            scope="day",
            dates=iso,
            day_label=_day_label_for(day_dates),
        )

    matched = _match_tasks_by_title(text, tasks)
    if matched and len(matched) <= 5:
        if not _LIST_INTENT.search(text) or len(matched) == 1:
            return TaskSummaryQuery(
                scope="task_match",
                matched_tasks=tuple(matched),
            )

    if _FULL_WEEK.search(text) and not re.search(
        r"\b(?:on|for)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b",
        text,
        re.I,
    ):
        return TaskSummaryQuery(scope="week")

    if _PROGRESS.search(text) and not _LIST_INTENT.search(text):
        return TaskSummaryQuery(scope="progress_only")

    return TaskSummaryQuery(scope="week")


def filter_tasks_by_dates(tasks: list[dict[str, Any]], dates: tuple[str, ...]) -> list[dict[str, Any]]:
    if not dates:
        return list(tasks)
    allowed = set(dates)
    return [t for t in tasks if str(t.get("task_date", ""))[:10] in allowed]
