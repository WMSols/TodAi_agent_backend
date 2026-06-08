"""Grounded plan + task context for goal chat and coaching (server-computed facts for LLM)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from todai.goal_planner.display import build_goal_plan_schedule_display, progress_counts
from todai.goal_planner.interrogation import format_skip_days, plan_skip_days
from todai.goal_planner.plan_resolver import plan_needs_task_setup
from todai.goal_planner.session_store import GoalPlanSessionStore

_INTENSE_MARKERS = re.compile(
    r"\b(hiit|high[- ]?intensity|progressive\s+overload|sprint|interval|"
    r"heavy|max|intense|challenging|advanced)\b",
    re.I,
)


def _intensity_band(plan_day: int, total_days: int) -> str:
    if total_days <= 1:
        return "stretch"
    ratio = plan_day / total_days
    if ratio <= 0.35:
        return "foundation"
    if ratio <= 0.7:
        return "building"
    return "stretch"


def _task_row_compact(row: dict[str, Any], *, plan_day: int, intensity: str) -> dict[str, Any]:
    st, en = row.get("start_time"), row.get("end_time")
    when = f"{st} – {en}" if st and en else "flexible"
    title = (row.get("title") or "Task").strip()
    return {
        "id": str(row.get("id") or ""),
        "title": title,
        "description": (row.get("description") or "").strip()[:400],
        "task_date": str(row.get("task_date", ""))[:10],
        "plan_day": plan_day,
        "intensity_band": intensity,
        "time": when,
        "status": (row.get("status") or "pending").lower(),
        "intense_hint": bool(_INTENSE_MARKERS.search(title + " " + (row.get("description") or ""))),
    }


def build_goal_plan_context(store: GoalPlanSessionStore, plan_id: str) -> dict[str, Any]:
    """Rich grounded snapshot: tasks by day, stats, skip days, hardest-day hints."""
    row = store.get_plan_row(plan_id) or {}
    tasks = store.list_goal_tasks(plan_id) if plan_id else []
    sess = store._load_plan_session(plan_id) or {}
    answers = sess.get("answers") or {}

    objective = ""
    if answers.get("objective", {}).get("parsed"):
        objective = str(answers["objective"]["parsed"])
    elif row.get("plan_notes"):
        objective = str(row.get("plan_notes") or "")

    goals = store.list_user_goals()
    gid = str(row.get("goal_id") or "")
    goal_title = ""
    goal_description = ""
    for g in goals:
        if str(g.get("id")) == gid:
            goal_title = str(g.get("title") or "")
            goal_description = str(g.get("description") or "")
            break

    from todai.goal_planner.today_context import get_server_today_for_user

    start_s = str(row.get("start_date") or "")[:10]
    end_s = str(row.get("end_date") or "")[:10]
    start = date.fromisoformat(start_s) if start_s else date.today()
    end = date.fromisoformat(end_s) if end_s else start
    total_days = max(1, (end - start).days + 1)

    skip_list = plan_skip_days(answers)
    skip_display = format_skip_days(skip_list)

    by_date: dict[str, list[dict[str, Any]]] = {}
    days_summary: list[dict[str, Any]] = []
    d = start
    plan_day = 1
    while d <= end:
        iso = d.isoformat()
        day_tasks = [t for t in tasks if str(t.get("task_date", ""))[:10] == iso]
        band = _intensity_band(plan_day, total_days)
        compact = [_task_row_compact(t, plan_day=plan_day, intensity=band) for t in day_tasks]
        by_date[iso] = compact
        prog = progress_counts(day_tasks)
        intense_count = sum(1 for c in compact if c.get("intense_hint"))
        days_summary.append(
            {
                "plan_day": plan_day,
                "date": iso,
                "weekday": d.strftime("%A"),
                "label": d.strftime("%A, %d %b"),
                "intensity_band": band,
                "task_count": len(day_tasks),
                "done": prog["done"],
                "pending": prog["pending"],
                "intense_task_count": intense_count,
                "is_skip_day": d.weekday() in set(skip_list) if skip_list else False,
            }
        )
        plan_day += 1
        d += timedelta(days=1)

    # Hardest days: later stretch days with tasks + intense titles
    active_days = [ds for ds in days_summary if ds["task_count"] > 0]
    ranked = sorted(
        active_days,
        key=lambda x: (
            x["intensity_band"] == "stretch",
            x["intense_task_count"],
            x["plan_day"],
        ),
        reverse=True,
    )
    hardest_days = ranked[:3]

    week_prog = progress_counts(tasks)
    schedule_display = None
    if tasks:
        schedule_display = build_goal_plan_schedule_display(
            tasks, start=start, end=end, goal_objective=objective
        )

    return {
        "plan_id": plan_id,
        "server_today": sess.get("server_today") or get_server_today_for_user(store.api_user_id),
        "goal_title": goal_title,
        "goal_description": goal_description,
        "objective": objective,
        "needs_setup": plan_needs_task_setup(store, plan_id, sess),
        "status": row.get("status"),
        "start_date": start_s,
        "end_date": end_s,
        "plan_days": total_days,
        "difficulty": row.get("difficulty") or "medium",
        "skip_days": skip_list,
        "skip_days_label": skip_display,
        "progress": week_prog,
        "days": days_summary,
        "tasks_by_date": by_date,
        "hardest_days": hardest_days,
        "task_count": len(tasks),
        "schedule_display": schedule_display,
    }
