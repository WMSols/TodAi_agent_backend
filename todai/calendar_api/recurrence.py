"""Expand weekly recurrence into local (naive) start/end datetimes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

WeeklyMode = Literal["same_day", "weekdays"]


def expand_weekly_occurrences(
    anchor_start: datetime,
    anchor_end: datetime,
    *,
    repeat_weeks: int,
    skip_days: set[int],
    weekly_mode: WeeklyMode = "same_day",
) -> list[tuple[datetime, datetime]]:
    """
    skip_days: weekday ints 0=Monday .. 6=Sunday (skipped, no occurrence).
    """
    if anchor_end <= anchor_start:
        raise ValueError("end must be after start")
    repeat_weeks = max(1, min(52, int(repeat_weeks)))
    duration = anchor_end - anchor_start
    start_time = anchor_start.time()
    anchor_date = anchor_start.date()
    anchor_weekday = anchor_date.weekday()
    out: list[tuple[datetime, datetime]] = []

    if weekly_mode == "same_day":
        for w in range(repeat_weeks):
            d = anchor_date + timedelta(weeks=w)
            if d.weekday() in skip_days:
                continue
            st = datetime.combine(d, start_time)
            out.append((st, st + duration))
        return out

    # weekdays: each week, every weekday not in skip_days (from anchor week onward)
    week0_monday = anchor_date - timedelta(days=anchor_weekday)
    for w in range(repeat_weeks):
        week_monday = week0_monday + timedelta(weeks=w)
        for offset in range(7):
            d = week_monday + timedelta(days=offset)
            if d.weekday() in skip_days:
                continue
            if w == 0 and d < anchor_date:
                continue
            st = datetime.combine(d, start_time)
            out.append((st, st + duration))
    return out
