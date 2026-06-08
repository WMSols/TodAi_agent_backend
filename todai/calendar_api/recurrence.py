"""Expand daily/weekly recurrence into local (naive) start/end datetimes."""

from __future__ import annotations

from datetime import datetime, timedelta


def expand_daily_occurrences(
    anchor_start: datetime,
    anchor_end: datetime,
    *,
    repeat_days: int,
) -> list[tuple[datetime, datetime]]:
    """Same time each calendar day for repeat_days occurrences (includes anchor day)."""
    if anchor_end <= anchor_start:
        raise ValueError("end must be after start")
    repeat_days = max(1, min(365, int(repeat_days)))
    duration = anchor_end - anchor_start
    start_time = anchor_start.time()
    anchor_date = anchor_start.date()
    out: list[tuple[datetime, datetime]] = []
    for d in range(repeat_days):
        day = anchor_date + timedelta(days=d)
        st = datetime.combine(day, start_time)
        out.append((st, st + duration))
    return out


def expand_weekly_occurrences(
    anchor_start: datetime,
    anchor_end: datetime,
    *,
    repeat_weeks: int,
    skip_days: set[int],
) -> list[tuple[datetime, datetime]]:
    """
    Each calendar week for repeat_weeks weeks, create an occurrence on every weekday
    not listed in skip_days (0=Monday .. 6=Sunday). Days before anchor_date in the
    first week are omitted.
    """
    if anchor_end <= anchor_start:
        raise ValueError("end must be after start")
    repeat_weeks = max(1, min(52, int(repeat_weeks)))
    duration = anchor_end - anchor_start
    start_time = anchor_start.time()
    anchor_date = anchor_start.date()
    week_start = anchor_date - timedelta(days=anchor_date.weekday())
    out: list[tuple[datetime, datetime]] = []
    for w in range(repeat_weeks):
        for dow in range(7):
            if dow in skip_days:
                continue
            day = week_start + timedelta(weeks=w, days=dow)
            if day < anchor_date:
                continue
            st = datetime.combine(day, start_time)
            out.append((st, st + duration))
    out.sort(key=lambda pair: pair[0])
    return out
