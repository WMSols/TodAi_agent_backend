"""
date_anchor.py — compact server date/time metadata for LLM prompts

Builds a small, authoritative anchor from server_date_utc (not hard-coded days):
  - today + current month summary
  - rolling next N calendar days (iso + weekday)
  - next occurrence of each weekday name
  - optional resolves for weekday words mentioned in the user message
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any

from todai.agent.routing.preview_range import (
    AGENT_WINDOW_DAYS,
    agent_window_bounds,
    resolve_weekday_lookup_bounds,
)
from todai.database.storage import parse_server_date

_WEEKDAY_TO_INDEX = {
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

_INDEX_TO_WEEKDAY = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_NAMES = "|".join(sorted(_WEEKDAY_TO_INDEX.keys(), key=len, reverse=True))
_WEEKDAY_PATTERN = re.compile(rf"\b({_WEEKDAY_NAMES})\b", re.I)
_NEXT_WEEKDAY_PATTERN = re.compile(rf"\bnext\s+({_WEEKDAY_NAMES})\b", re.I)
_NEXT_MONTH_PATTERN = re.compile(r"\bnext\s+month\b", re.I)
_NEXT_MONTH_WEEKDAY = re.compile(rf"\bnext\s+month\s+({_WEEKDAY_NAMES})\b", re.I)


def _next_weekday_on_or_after(today: date, weekday_index: int) -> date:
    delta = (weekday_index - today.weekday()) % 7
    return today + timedelta(days=delta)


def _month_summary(anchor: date) -> dict[str, Any]:
    y, m = anchor.year, anchor.month
    first = date(y, m, 1)
    last_n = calendar.monthrange(y, m)[1]
    last = date(y, m, last_n)
    return {
        "ym": f"{y:04d}-{m:02d}",
        "label": first.strftime("%B %Y"),
        "days_in_month": last_n,
        "first_day": {"iso": first.isoformat(), "weekday": first.strftime("%A")},
        "last_day": {"iso": last.isoformat(), "weekday": last.strftime("%A")},
    }


def _cap_weekday_to_agent_window(d: date, today: date) -> str | None:
    win_start, win_end = agent_window_bounds(today)
    if d < win_start or d > win_end:
        return None
    return d.isoformat()


def weekdays_ahead(today: date) -> dict[str, str]:
    """Next occurrence of each weekday on or after today, only if inside the agent window."""
    out: dict[str, str] = {}
    for idx, name in enumerate(_INDEX_TO_WEEKDAY):
        iso = _cap_weekday_to_agent_window(_next_weekday_on_or_after(today, idx), today)
        if iso:
            out[name.lower()] = iso
    return out


def _span_used(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < u_end and end > u_start for u_start, u_end in used)


def _first_weekday_on_or_after(start: date, end: date, weekday_index: int) -> date | None:
    d = start
    while d <= end:
        if d.weekday() == weekday_index:
            return d
        d += timedelta(days=1)
    return None


def _weekday_option(d: date) -> dict[str, str]:
    return {"iso": d.isoformat(), "label": d.strftime("%A, %d %B %Y")}


def weekdays_in_range(start: date, end: date, weekday_index: int) -> list[dict[str, str]]:
    """All matching weekdays between start and end (inclusive)."""
    if end < start:
        return []
    out: list[dict[str, str]] = []
    d = start
    while d <= end:
        if d.weekday() == weekday_index:
            out.append(_weekday_option(d))
        d += timedelta(days=1)
    return out


def weekdays_in_agent_window(today: date, weekday_index: int) -> list[dict[str, str]]:
    win_start, win_end = agent_window_bounds(today)
    return weekdays_in_range(win_start, win_end, weekday_index)


def _shift_calendar_month_start(today: date, delta_months: int) -> tuple[date, date]:
    m0 = (today.year * 12 + (today.month - 1)) + delta_months
    year = m0 // 12
    month = m0 % 12 + 1
    last_n = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_n)


def _resolve_plain_weekday(
    today: date,
    weekday_index: int,
    *,
    month_bounds: tuple[date, date] | None,
) -> tuple[str | None, list[dict[str, str]] | None]:
    """
    Plain \"friday\" (no next/this): one date, or candidates when several fall in the agent window.
    If today is that weekday and multiple exist, prefer today (e.g. \"on wednesday\" when today is Wed).
    """
    if month_bounds:
        m_start, m_end = month_bounds
        options = weekdays_in_range(m_start, m_end, weekday_index)
    else:
        options = weekdays_in_agent_window(today, weekday_index)

    if not options:
        return None, None
    if len(options) == 1:
        return options[0]["iso"], None
    if today.weekday() == weekday_index:
        today_iso = today.isoformat()
        if any(o["iso"] == today_iso for o in options):
            return today_iso, None
    return None, options


def resolve_weekday_context(
    message: str,
    today: date,
    *,
    anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Weekday words in the message → mentioned_weekdays (unambiguous) and/or weekday_candidates.
    """
    found: dict[str, str] = {}
    candidates: dict[str, list[dict[str, str]]] = {}
    text = message or ""
    used_spans: list[tuple[int, int]] = []
    month_bounds = resolve_weekday_lookup_bounds(text, today, anchor)

    win_start, win_end = agent_window_bounds(today)

    if _NEXT_MONTH_PATTERN.search(text):
        m_start, m_end = _shift_calendar_month_start(today, 1)
        m_start = max(m_start, win_start)
        m_end = min(m_end, win_end)
        for match in _NEXT_MONTH_WEEKDAY.finditer(text):
            key = match.group(1).lower()
            idx = _WEEKDAY_TO_INDEX.get(key)
            if idx is None:
                continue
            canonical = _INDEX_TO_WEEKDAY[idx].lower()
            d = _first_weekday_on_or_after(m_start, m_end, idx) if m_end >= m_start else None
            if d:
                iso = _cap_weekday_to_agent_window(d, today)
                if iso:
                    found[canonical] = iso
            used_spans.append(match.span())

    for match in _NEXT_WEEKDAY_PATTERN.finditer(text):
        key = match.group(1).lower()
        idx = _WEEKDAY_TO_INDEX.get(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        first = _next_weekday_on_or_after(today, idx)
        d = first + timedelta(days=7) if first == today else first
        iso = _cap_weekday_to_agent_window(d, today)
        if iso:
            found[canonical] = iso
        used_spans.append(match.span())

    for match in _WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _WEEKDAY_TO_INDEX.get(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        if canonical in found or canonical in candidates:
            continue
        iso, opts = _resolve_plain_weekday(
            today,
            idx,
            month_bounds=month_bounds,
        )
        if iso:
            found[canonical] = iso
        elif opts:
            candidates[canonical] = opts

    out: dict[str, Any] = {}
    if found:
        out["mentioned_weekdays"] = found
    if candidates:
        out["weekday_candidates"] = candidates
    return out


def resolve_weekdays_in_text(
    message: str,
    today: date,
    *,
    anchor: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Map weekday words → ISO date when unambiguous (see resolve_weekday_context)."""
    return resolve_weekday_context(message, today, anchor=anchor).get("mentioned_weekdays") or {}


def build_date_anchor(
    storage_index: dict[str, Any] | None,
    *,
    message: str | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any]:
    """
    Compact metadata for router/specialist prompts.
    All dates are derived from server_date_utc in the storage index.
    """
    today = parse_server_date(storage_index)
    days = horizon_days if horizon_days is not None else AGENT_WINDOW_DAYS
    now_raw = (storage_index or {}).get("server_datetime_utc")
    now_utc = str(now_raw)[:16] if now_raw else None

    rolling: list[dict[str, Any]] = []
    for i in range(days):
        d = today + timedelta(days=i)
        rolling.append(
            {
                "iso": d.isoformat(),
                "weekday": d.strftime("%A"),
                "day": d.day,
                "offset": i,
            }
        )

    anchor: dict[str, Any] = {
        "today": {
            "iso": today.isoformat(),
            "weekday": today.strftime("%A"),
            "label": today.strftime("%A, %d %B %Y"),
        },
        "now_utc": now_utc,
        "month": _month_summary(today),
        "rolling_days": rolling,
        "weekday_lookup": weekdays_ahead(today),
    }
    if message:
        wctx = resolve_weekday_context(message, today, anchor={"month": anchor["month"]})
        if wctx.get("mentioned_weekdays"):
            anchor["mentioned_weekdays"] = wctx["mentioned_weekdays"]
        if wctx.get("weekday_candidates"):
            anchor["weekday_candidates"] = wctx["weekday_candidates"]
    return anchor
