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
    "thursaday": 3,
    "thursady": 3,
    "thurday": 3,
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
_COMING_WEEKDAY_PATTERN = re.compile(rf"\b(?:coming|upcoming)\s+({_WEEKDAY_NAMES})\b", re.I)
_THIS_WEEKDAY_PATTERN = re.compile(rf"\bthis\s+({_WEEKDAY_NAMES})\b", re.I)
_NEXT_COMING_WEEKDAY_PATTERN = re.compile(
    rf"\bnext\s+(?:coming|upcoming)\s+({_WEEKDAY_NAMES})\b",
    re.I,
)
_THIS_AND_NEXT_WEEKDAY = re.compile(
    rf"\bthis\s+({_WEEKDAY_NAMES})\s+and\s+(?:on\s+)?next\s+\1\b",
    re.I,
)
_NEXT_MONTH_PATTERN = re.compile(r"\bnext\s+month\b", re.I)
_NEXT_MONTH_WEEKDAY = re.compile(rf"\bnext\s+month\s+({_WEEKDAY_NAMES})\b", re.I)


def _next_weekday_on_or_after(today: date, weekday_index: int) -> date:
    delta = (weekday_index - today.weekday()) % 7
    return today + timedelta(days=delta)


def message_has_next_named_weekday(message: str) -> bool:
    return bool(_NEXT_WEEKDAY_PATTERN.search(message or ""))


def message_has_coming_named_weekday(message: str) -> bool:
    return bool(_COMING_WEEKDAY_PATTERN.search(message or ""))


def message_has_this_named_weekday(message: str) -> bool:
    return bool(_THIS_WEEKDAY_PATTERN.search(message or ""))


def message_has_named_weekday_target(message: str) -> bool:
    """True when message names a weekday with this/coming/upcoming/next (not plain weekday alone)."""
    text = message or ""
    return bool(
        _NEXT_WEEKDAY_PATTERN.search(text)
        or _COMING_WEEKDAY_PATTERN.search(text)
        or _THIS_WEEKDAY_PATTERN.search(text)
    )


def multi_resolved_weekday_isos(date_anchor: dict[str, Any] | None) -> list[str]:
    """Sorted unique ISO dates from day_targets or mentioned_weekdays (2+ → multi-target)."""
    anchor = date_anchor or {}
    day_targets = anchor.get("day_targets") or []
    if day_targets:
        isos = sorted(
            {
                str(t.get("iso") or "")[:10]
                for t in day_targets
                if len(str(t.get("iso") or "")[:10]) == 10
            }
        )
        if isos:
            return isos
    mentioned = anchor.get("mentioned_weekdays") or {}
    return sorted(
        {
            str(v)[:10]
            for v in mentioned.values()
            if len(str(v)[:10]) == 10
        }
    )


def day_target_isos(date_anchor: dict[str, Any] | None) -> list[str]:
    """Ordered ISO list from day_targets (preserves user phrasing order when set)."""
    out: list[str] = []
    for t in (date_anchor or {}).get("day_targets") or []:
        iso = str(t.get("iso") or "")[:10]
        if len(iso) == 10 and iso not in out:
            out.append(iso)
    return out


_WEEKDAY_TYPO_IN_TEXT = (
    (re.compile(r"\bthursaday\b", re.I), "thursday"),
    (re.compile(r"\bthursady\b", re.I), "thursday"),
    (re.compile(r"\bthurday\b", re.I), "thursday"),
)


def _normalize_weekday_typos_in_text(text: str) -> str:
    out = text or ""
    for pattern, replacement in _WEEKDAY_TYPO_IN_TEXT:
        out = pattern.sub(replacement, out)
    return out


def _weekday_index_from_token(raw: str) -> int | None:
    key = (raw or "").lower().strip()
    idx = _WEEKDAY_TO_INDEX.get(key)
    if idx is not None:
        return idx
    if key.endswith("day") and len(key) >= 5:
        for prefix_len in range(3, min(6, len(key) - 2)):
            prefix = key[:prefix_len]
            for name, i in _WEEKDAY_TO_INDEX.items():
                if len(name) >= 4 and name.startswith(prefix) and name.endswith("day"):
                    return i
    return None


def _next_unused_weekday_iso(
    today: date,
    weekday_index: int,
    used_isos: set[str],
) -> str | None:
    for opt in weekdays_in_agent_window(today, weekday_index):
        iso = (opt.get("iso") or "")[:10]
        if len(iso) == 10 and iso not in used_isos:
            return iso
    return None


def _resolve_target_iso_for_qualifier(
    today: date,
    weekday_index: int,
    qualifier: str,
    used_isos: set[str],
) -> str | None:
    if qualifier == "next":
        iso = _resolve_next_named_weekday_iso(today, weekday_index)
    elif qualifier in ("this", "coming"):
        iso = _resolve_coming_named_weekday_iso(today, weekday_index)
    else:
        iso = _next_unused_weekday_iso(today, weekday_index, used_isos)
    if iso and iso in used_isos:
        iso = _next_unused_weekday_iso(today, weekday_index, used_isos)
    return iso


def _span_overlaps(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    s0, e0 = span
    return any(not (e0 <= s1 or e1 <= s0) for s1, e1 in used)


def resolve_day_targets(message: str, today: date) -> list[dict[str, str]]:
    """
    Each qualified weekday phrase in message order → one ISO (allows same weekday twice).
    e.g. this sunday + coming sunday → nearest Sunday and the following Sunday in window.
    """
    text = _normalize_weekday_typos_in_text(message or "")
    hits: list[tuple[int, int, str, str]] = []
    used_spans: list[tuple[int, int]] = []

    def _add(pattern: re.Pattern[str], qualifier: str) -> None:
        for match in pattern.finditer(text):
            if _span_overlaps(match.span(), used_spans):
                continue
            raw = match.group(1).lower()
            if _weekday_index_from_token(raw) is None:
                continue
            hits.append((match.start(), match.end(), qualifier, raw))
            used_spans.append(match.span())

    for match in _THIS_AND_NEXT_WEEKDAY.finditer(text):
        raw = match.group(1).lower()
        idx = _weekday_index_from_token(raw)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        options = weekdays_in_agent_window(today, idx)
        if len(options) >= 2:
            hits.append((match.start(), match.end(), "this", raw))
            hits.append((match.start(), match.end(), "next", raw))
        elif len(options) == 1:
            hits.append((match.start(), match.end(), "this", raw))
        used_spans.append(match.span())

    _add(_NEXT_COMING_WEEKDAY_PATTERN, "next")
    _add(_THIS_WEEKDAY_PATTERN, "this")
    _add(_COMING_WEEKDAY_PATTERN, "coming")
    _add(_NEXT_WEEKDAY_PATTERN, "next")

    hits.sort(key=lambda h: (h[0], -h[1]))
    targets: list[dict[str, str]] = []
    used_isos: set[str] = set()

    for start, end, qualifier, raw in hits:
        idx = _weekday_index_from_token(raw)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        iso = _resolve_target_iso_for_qualifier(today, idx, qualifier, used_isos)
        if not iso:
            continue
        used_isos.add(iso)
        targets.append(
            {
                "weekday": canonical,
                "iso": iso,
                "qualifier": qualifier,
                "phrase": text[start:end].strip(),
                "label": date.fromisoformat(iso[:10]).strftime("%A, %d %B %Y"),
            }
        )

    return targets


def message_has_whole_week_phrase(message: str) -> bool:
    m = (message or "").lower()
    return any(
        p in m
        for p in (
            "next week",
            "next all week",
            "all of next week",
            "all next week",
            "coming week",
            "this week",
        )
    )


def single_day_iso_from_anchor(date_anchor: dict[str, Any] | None) -> str | None:
    day_targets = (date_anchor or {}).get("day_targets") or []
    if len(day_targets) == 1:
        iso = str(day_targets[0].get("iso") or "")[:10]
        return iso if len(iso) == 10 else None
    mentioned = (date_anchor or {}).get("mentioned_weekdays") or {}
    if len(mentioned) != 1:
        return None
    iso = str(next(iter(mentioned.values())))[:10]
    return iso if len(iso) == 10 else None


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


def _resolve_next_named_weekday_iso(today: date, weekday_index: int) -> str | None:
    """\"next saturday\" on Friday → second Saturday in agent window (e.g. 30 May), not 23 May."""
    options = weekdays_in_agent_window(today, weekday_index)
    if len(options) >= 2:
        return options[1]["iso"]
    if len(options) == 1:
        d0 = date.fromisoformat(options[0]["iso"][:10])
        d1 = d0 + timedelta(days=7)
        return _cap_weekday_to_agent_window(d1, today)
    return None


def _resolve_coming_named_weekday_iso(today: date, weekday_index: int) -> str | None:
    """\"coming sunday\" → nearest upcoming that weekday in agent window (first match)."""
    options = weekdays_in_agent_window(today, weekday_index)
    if options:
        return options[0]["iso"]
    return None


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
    text = _normalize_weekday_typos_in_text(message or "")
    used_spans: list[tuple[int, int]] = []
    month_bounds = resolve_weekday_lookup_bounds(text, today, anchor)

    win_start, win_end = agent_window_bounds(today)

    if _NEXT_MONTH_PATTERN.search(text):
        m_start, m_end = _shift_calendar_month_start(today, 1)
        m_start = max(m_start, win_start)
        m_end = min(m_end, win_end)
        for match in _NEXT_MONTH_WEEKDAY.finditer(text):
            key = match.group(1).lower()
            idx = _weekday_index_from_token(key)
            if idx is None:
                continue
            canonical = _INDEX_TO_WEEKDAY[idx].lower()
            d = _first_weekday_on_or_after(m_start, m_end, idx) if m_end >= m_start else None
            if d:
                iso = _cap_weekday_to_agent_window(d, today)
                if iso:
                    found[canonical] = iso
            used_spans.append(match.span())

    for match in _NEXT_COMING_WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        if canonical in found or canonical in candidates:
            continue
        iso = _resolve_next_named_weekday_iso(today, idx)
        if iso:
            found[canonical] = iso
        used_spans.append(match.span())

    for match in _THIS_AND_NEXT_WEEKDAY.finditer(text):
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        options = weekdays_in_agent_window(today, idx)
        if len(options) >= 2:
            candidates[canonical] = options
        elif len(options) == 1:
            found[canonical] = options[0]["iso"]
        used_spans.append(match.span())

    for match in _COMING_WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        if canonical in found or canonical in candidates:
            continue
        iso = _resolve_coming_named_weekday_iso(today, idx)
        if iso:
            found[canonical] = iso
        used_spans.append(match.span())

    for match in _THIS_WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        if canonical in found or canonical in candidates:
            continue
        iso = _resolve_coming_named_weekday_iso(today, idx)
        if iso:
            found[canonical] = iso
        used_spans.append(match.span())

    for match in _NEXT_WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
        if idx is None:
            continue
        canonical = _INDEX_TO_WEEKDAY[idx].lower()
        if canonical in found or canonical in candidates:
            continue
        iso = _resolve_next_named_weekday_iso(today, idx)
        if iso:
            found[canonical] = iso
        used_spans.append(match.span())

    for match in _WEEKDAY_PATTERN.finditer(text):
        if _span_used(match.span(), used_spans):
            continue
        key = match.group(1).lower()
        idx = _weekday_index_from_token(key)
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
        day_targets = resolve_day_targets(message, today)
        if day_targets:
            anchor["day_targets"] = day_targets
        wctx = resolve_weekday_context(message, today, anchor={"month": anchor["month"]})
        if wctx.get("mentioned_weekdays"):
            anchor["mentioned_weekdays"] = wctx["mentioned_weekdays"]
        if wctx.get("weekday_candidates"):
            anchor["weekday_candidates"] = wctx["weekday_candidates"]
        if day_targets and not wctx.get("mentioned_weekdays"):
            anchor["mentioned_weekdays"] = {
                t["weekday"]: t["iso"] for t in day_targets if t.get("weekday") and t.get("iso")
            }
    return anchor
