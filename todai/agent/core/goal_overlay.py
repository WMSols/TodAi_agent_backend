"""Merge Supabase goal tasks into calendar schedule_display (read-only)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from todai.agent.core.display import _empty_day_row, build_schedule_display
from todai.database.config import use_local_storage
from todai.goal_planner.display import _fmt_time, _status_label
from todai.goal_planner.session_store import GoalPlanSessionStore


def merge_goal_tasks_into_display(
    display: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    *,
    period_from: str,
    period_to: str,
    title: str | None = None,
) -> dict[str, Any] | None:
    """Append goal_task slots to an existing schedule display (or build days from tasks)."""
    if not tasks and not display:
        return display
    try:
        start = date.fromisoformat(period_from[:10])
        end = date.fromisoformat(period_to[:10])
    except ValueError:
        return display

    if display is None:
        display = {
            "schema": "todai.schedule.v1",
            "type": "schedule",
            "title": title or "Your schedule",
            "empty": True,
            "period": {"from": period_from[:10], "to": period_to[:10]},
            "days": [],
            "free_days": [],
        }

    by_date: dict[str, dict[str, Any]] = {}
    for day in display.get("days") or []:
        iso = str(day.get("date", ""))[:10]
        if iso:
            by_date[iso] = day

    d = start
    while d <= end:
        iso = d.isoformat()
        if iso not in by_date:
            by_date[iso] = _empty_day_row(datetime.combine(d, time.min))
        d = date.fromordinal(d.toordinal() + 1)

    for t in tasks:
        iso = str(t.get("task_date", ""))[:10]
        if iso not in by_date:
            continue
        st_label = t.get("start_time")
        en_label = t.get("end_time")
        when = (
            f"{_fmt_time(st_label)} – {_fmt_time(en_label)}"
            if st_label and en_label
            else "time: flexible"
        )
        st = _status_label(t.get("status"))
        by_date[iso]["slots"].append(
            {
                "time": when,
                "title": (t.get("title") or "Goal task").strip(),
                "description": (t.get("description") or "").strip(),
                "status": st,
                "kind": "goal_task",
            }
        )

    out = dict(display)
    out["days"] = [by_date[k] for k in sorted(by_date.keys())]
    out["empty"] = not any((day.get("slots") or []) for day in out["days"])
    if title:
        out["title"] = title
    elif out.get("type") == "schedule":
        out["title"] = "Your schedule (calendar + goals)"
    return out


def fetch_goal_tasks_for_period(user_id: str, period_from: str, period_to: str) -> list[dict[str, Any]]:
    if use_local_storage():
        return []
    try:
        start = date.fromisoformat(period_from[:10])
        end = date.fromisoformat(period_to[:10])
    except ValueError:
        return []
    store = GoalPlanSessionStore(user_id)
    return store.list_goal_tasks_in_range(start, end)


def build_schedule_display_with_goals(
    tool_results: list[dict[str, Any]],
    *,
    user_id: str,
    period_from: str | None = None,
    period_to: str | None = None,
    fill_empty_days: bool = True,
    title: str | None = None,
    show_free_banners: bool = False,
) -> dict[str, Any] | None:
    display = build_schedule_display(
        tool_results,
        period_from=period_from,
        period_to=period_to,
        fill_empty_days=fill_empty_days,
        title=title,
        show_free_banners=show_free_banners,
    )
    p_from = (period_from or (display or {}).get("period", {}).get("from") or "")[:10]
    p_to = (period_to or (display or {}).get("period", {}).get("to") or "")[:10]
    if not p_from or not p_to:
        return display
    tasks = fetch_goal_tasks_for_period(user_id, p_from, p_to)
    if not tasks and not display:
        return display
    return merge_goal_tasks_into_display(
        display,
        tasks,
        period_from=p_from,
        period_to=p_to,
        title=title,
    )


