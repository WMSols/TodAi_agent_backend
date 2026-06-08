"""Build 7-day goal_tasks from answers + calendar free-time tool data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from todai.agent.tools.calendar import execute_read_tools
from todai.database import user_store


def fetch_plan_window_schedule(user_id: str, start: date, end: date) -> list[dict[str, Any]]:
    with user_store(user_id) as store:
        results, _ = execute_read_tools(
            store,
            [
                {
                    "tool": "get_free_time",
                    "arguments": {"from": start.isoformat(), "to": end.isoformat()},
                },
                {
                    "tool": "get_schedule_range",
                    "arguments": {"from": start.isoformat(), "to": end.isoformat()},
                },
            ],
        )
    return results


def build_tasks_from_free_time(
    *,
    objective: str,
    difficulty: str,
    tasks_per_day: int,
    minutes_per_day: int,
    start: date,
    days: int,
    free_time_data: dict[str, Any],
    skip_days: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Place tasks into free gaps; deterministic (no LLM). Skips weekdays in skip_days (0=Mon..6=Sun)."""
    per_task = max(5, minutes_per_day // max(1, tasks_per_day))
    skip_set = set(skip_days or [])
    day_rows = {d["date"]: d for d in (free_time_data.get("days") or []) if d.get("date")}
    tasks: list[dict[str, Any]] = []
    active_day_index = 0
    for offset in range(days):
        d = start + timedelta(days=offset)
        if d.weekday() in skip_set:
            continue
        active_day_index += 1
        iso = d.isoformat()
        day_info = day_rows.get(iso) or {"date": iso, "free_gaps": []}
        gaps = _sorted_gaps(day_info.get("free_gaps") or [])
        placed = 0
        for gap in gaps:
            if placed >= tasks_per_day:
                break
            start_s, end_s = gap.get("start"), gap.get("end")
            if not start_s or not end_s:
                continue
            try:
                gs = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
                ge = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
            except ValueError:
                continue
            if gs.tzinfo:
                gs, ge = gs.replace(tzinfo=None), ge.replace(tzinfo=None)
            gap_mins = int((ge - gs).total_seconds() // 60)
            if gap_mins < per_task:
                continue
            if _is_overnight_noise(gs, ge, per_task) or gs.hour < 6:
                continue
            task_end = gs + timedelta(minutes=per_task)
            placed += 1
            n = placed
            tasks.append(
                {
                    "task_date": iso,
                    "title": "Goal task",
                    "description": "",
                    "start_time": gs.strftime("%H:%M:%S"),
                    "end_time": task_end.strftime("%H:%M:%S"),
                    "sort_order": n - 1,
                    "_day_index": active_day_index,
                    "_task_num": n,
                }
            )
        while placed < tasks_per_day:
            placed += 1
            n = placed
            tasks.append(
                {
                    "task_date": iso,
                    "title": "Goal task",
                    "description": "",
                    "start_time": None,
                    "end_time": None,
                    "sort_order": n - 1,
                    "_day_index": active_day_index,
                    "_task_num": n,
                }
            )
    return tasks


def _sorted_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[tuple[int, int, dict[str, Any]]] = []
    for gap in gaps:
        start_s, end_s = gap.get("start"), gap.get("end")
        if not start_s or not end_s:
            continue
        try:
            gs = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
            ge = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
            if gs.tzinfo:
                gs, ge = gs.replace(tzinfo=None), ge.replace(tzinfo=None)
        except ValueError:
            continue
        priority = 0
        if gs.hour < 6:
            priority = 2
        elif gs.hour >= 22:
            priority = 1
        parsed.append((priority, gs.hour * 60 + gs.minute, gap))
    parsed.sort(key=lambda x: (x[0], x[1]))
    return [g for _, _, g in parsed]


def _is_overnight_noise(gs: datetime, ge: datetime, per_task: int) -> bool:
    """Skip tiny slots at midnight (often artifact gaps)."""
    if gs.hour == 0 and gs.minute == 0:
        span = int((ge - gs).total_seconds() // 60)
        if span <= per_task + 5:
            return True
    return False
