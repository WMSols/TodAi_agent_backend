"""Format goal tasks + calendar for goal planner replies and UI schedule panel."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from todai.agent.core.schedule_display import (
    _empty_day_row,
    build_schedule_display,
    format_block_line,
    format_schedule_read_results,
)


def _fmt_time(t: str | None) -> str:
    if not t:
        return "flexible"
    try:
        parts = str(t).split(":")
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        hour12 = h % 12 or 12
        suffix = "am" if h < 12 else "pm"
        if m == 0:
            return f"{hour12} {suffix}"
        return f"{hour12}:{m:02d} {suffix}"
    except (ValueError, IndexError):
        return str(t)


def _status_label(status: str | None) -> str:
    s = (status or "pending").lower()
    if s in ("done", "completed"):
        return "done"
    if s in ("skipped", "cancelled"):
        return "skipped"
    return "pending"


def build_goal_plan_schedule_display(
    tasks: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    title: str = "7-day goal plan",
    goal_objective: str = "",
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calendar-style JSON for `renderScheduleDisplay` in the web UI."""
    by_date: dict[str, dict[str, Any]] = {}
    d = start
    while d <= end:
        by_date[d.isoformat()] = _empty_day_row(datetime.combine(d, datetime.min.time()))
        d = date.fromordinal(d.toordinal() + 1)

    objective_label = (goal_objective or "").strip()
    done = pending = skipped = 0
    for t in tasks:
        iso = str(t.get("task_date", ""))[:10]
        if iso not in by_date:
            by_date[iso] = _empty_day_row(datetime.fromisoformat(iso))
        st = _status_label(t.get("status"))
        if st == "done":
            done += 1
        elif st == "skipped":
            skipped += 1
        else:
            pending += 1
        st_label = t.get("start_time")
        en_label = t.get("end_time")
        when = (
            f"{_fmt_time(st_label)} – {_fmt_time(en_label)}"
            if st_label and en_label
            else "time: flexible"
        )
        task_title = (t.get("title") or "Task").strip()
        desc = (t.get("description") or "").strip()
        slot: dict[str, Any] = {
            "time": when,
            "title": task_title,
            "description": desc,
            "status": st,
            "kind": "goal_task",
        }
        if objective_label:
            slot["goal_objective"] = objective_label
        by_date[iso]["slots"].append(slot)

    if tool_results:
        cal = build_schedule_display(
            tool_results,
            period_from=start.isoformat(),
            period_to=end.isoformat(),
            fill_empty_days=False,
            title="Calendar events",
        )
        if cal:
            for day in cal.get("days") or []:
                iso = day.get("date")
                if iso not in by_date:
                    continue
                for slot in day.get("slots") or []:
                    by_date[iso]["slots"].append(
                        {
                            "time": slot.get("time", ""),
                            "title": slot.get("title", "Event"),
                            "description": "",
                            "status": "calendar",
                            "kind": "calendar_event",
                        }
                    )

    total = done + pending + skipped
    progress_pct = int((done / total) * 100) if total else 0

    return {
        "schema": "todai.schedule.v1",
        "type": "goal_plan",
        "title": title,
        "empty": total == 0,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "days": [by_date[k] for k in sorted(by_date.keys())],
        "progress": {
            "total": total,
            "done": done,
            "pending": pending,
            "skipped": skipped,
            "percent": progress_pct,
        },
    }


def progress_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    done = pending = skipped = 0
    for t in tasks:
        st = _status_label(t.get("status"))
        if st == "done":
            done += 1
        elif st == "skipped":
            skipped += 1
        else:
            pending += 1
    total = done + pending + skipped
    percent = int((done / total) * 100) if total else 0
    return {
        "total": total,
        "done": done,
        "pending": pending,
        "skipped": skipped,
        "percent": percent,
    }


def format_progress_header(
    tasks: list[dict[str, Any]],
    *,
    label: str = "Progress",
) -> str:
    prog = progress_counts(tasks)
    if not prog["total"]:
        return ""
    return (
        f"**{label}:** {prog['done']}/{prog['total']} done "
        f"({prog['percent']}%) · {prog['pending']} pending"
    )


def format_plan_week_by_day(
    tasks: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    title: str = "Goal tasks (this plan)",
) -> str:
    """Every plan day from start→end; empty days show no tasks (matches calendar preview)."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        d = str(t.get("task_date", ""))[:10]
        by_date.setdefault(d, []).append(t)
    lines = [title + ":", ""]
    cur = start
    while cur <= end:
        iso = cur.isoformat()
        day_label = cur.strftime("%A, %d %b")
        lines.append(f"**{day_label}**")
        day_tasks = by_date.get(iso, [])
        if not day_tasks:
            lines.append("  _No tasks scheduled._")
        else:
            for row in sorted(day_tasks, key=lambda x: int(x.get("sort_order") or 0)):
                st, en = row.get("start_time"), row.get("end_time")
                when = f"{_fmt_time(st)} – {_fmt_time(en)}" if st and en else "flexible"
                status = _status_label(row.get("status"))
                lines.append(
                    f"  • [{status}] {(row.get('title') or 'Task').strip()} ({when})"
                )
        lines.append("")
        cur += timedelta(days=1)
    lines.append(
        "_Ask about a specific day (e.g. **Wednesday tasks**) or use **my schedule** for calendar + free time._"
    )
    return "\n".join(lines).strip()


def format_goal_tasks_brief(tasks: list[dict[str, Any]], *, title: str = "Goal tasks (this plan)") -> str:
    """Compact text summary — goal tasks only, no calendar or free-time blocks."""
    if not tasks:
        return "No goal tasks for this plan yet."
    by_date: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        d = str(t.get("task_date", ""))[:10]
        by_date.setdefault(d, []).append(t)
    lines = [title + ":", ""]
    for d in sorted(by_date.keys()):
        try:
            dt = date.fromisoformat(d)
            day_label = dt.strftime("%A, %d %b")
        except ValueError:
            day_label = d
        lines.append(f"**{day_label}**")
        for row in sorted(by_date[d], key=lambda x: int(x.get("sort_order") or 0)):
            st, en = row.get("start_time"), row.get("end_time")
            when = f"{_fmt_time(st)} – {_fmt_time(en)}" if st and en else "flexible"
            status = _status_label(row.get("status"))
            lines.append(f"  • [{status}] {(row.get('title') or 'Task').strip()} ({when})")
        lines.append("")
    lines.append(
        "_Ask about a specific day (e.g. **Wednesday tasks**) or use **my schedule** for calendar + free time._"
    )
    return "\n".join(lines).strip()


def format_goal_tasks_detail(tasks: list[dict[str, Any]], *, title: str = "Task details") -> str:
    """One or more tasks with descriptions (for name-specific questions)."""
    if not tasks:
        return "No matching tasks found."
    lines = [title + ":", ""]
    for row in tasks:
        st, en = row.get("start_time"), row.get("end_time")
        when = f"{_fmt_time(st)} – {_fmt_time(en)}" if st and en else "flexible"
        status = _status_label(row.get("status"))
        d = str(row.get("task_date", ""))[:10]
        try:
            day_label = date.fromisoformat(d).strftime("%A, %d %b")
        except ValueError:
            day_label = d
        lines.append(f"**{(row.get('title') or 'Task').strip()}** ({day_label}, {when}) · [{status}]")
        desc = (row.get("description") or "").strip()
        if desc:
            lines.append(desc)
        lines.append("")
    return "\n".join(lines).strip()


def format_goal_tasks(tasks: list[dict[str, Any]], *, title: str = "Your 7-day goal tasks") -> str:
    if not tasks:
        return "No goal tasks found for this plan yet."
    by_date: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        d = str(t.get("task_date", ""))[:10]
        by_date.setdefault(d, []).append(t)
    lines = [title + ":", ""]
    for d in sorted(by_date.keys()):
        try:
            dt = date.fromisoformat(d)
            day_label = dt.strftime("%A, %d %b")
        except ValueError:
            day_label = d
        lines.append(f"**{day_label}**")
        for row in sorted(by_date[d], key=lambda x: int(x.get("sort_order") or 0)):
            st, en = row.get("start_time"), row.get("end_time")
            when = f"{_fmt_time(st)} – {_fmt_time(en)}" if st and en else "time: flexible"
            status = _status_label(row.get("status"))
            lines.append(f"  • [{status}] {row.get('title', 'Task')} ({when})")
            desc = (row.get("description") or "").strip()
            if desc:
                lines.append(f"    {desc}")
        lines.append("")
    return "\n".join(lines).strip()


def format_free_time_summary(free_data: dict[str, Any] | None) -> str:
    if not free_data:
        return ""
    days = free_data.get("days") or []
    if not days:
        return ""
    lines = ["**Calendar free time (summary):**", ""]
    for day in days[:7]:
        iso = day.get("date", "")
        gaps = day.get("free_gaps") or []
        if not gaps:
            lines.append(f"• {iso}: no free gaps listed")
            continue
        if len(gaps) == 1:
            g = gaps[0]
            try:
                gs = datetime.fromisoformat(str(g.get("start", "")).replace("Z", "+00:00"))
                ge = datetime.fromisoformat(str(g.get("end", "")).replace("Z", "+00:00"))
                if gs.tzinfo:
                    gs, ge = gs.replace(tzinfo=None), ge.replace(tzinfo=None)
                span_mins = int((ge - gs).total_seconds() // 60)
                if span_mins >= 20 * 60:
                    lines.append(f"• {iso}: mostly free (all day)")
                    continue
            except ValueError:
                pass
        parts = []
        for g in gaps[:3]:
            try:
                gs = datetime.fromisoformat(str(g.get("start", "")).replace("Z", "+00:00"))
                ge = datetime.fromisoformat(str(g.get("end", "")).replace("Z", "+00:00"))
                if gs.tzinfo:
                    gs, ge = gs.replace(tzinfo=None), ge.replace(tzinfo=None)
                parts.append(f"{gs.strftime('%H:%M')}–{ge.strftime('%H:%M')}")
            except ValueError:
                continue
        extra = f" (+{len(gaps) - 3} more)" if len(gaps) > 3 else ""
        lines.append(f"• {iso}: {', '.join(parts) or 'open'}{extra}")
    return "\n".join(lines)


def format_plan_schedule_reply(
    *,
    tasks: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    start: date,
    end: date,
    schedule_display: dict[str, Any] | None = None,
) -> str:
    prog = (schedule_display or {}).get("progress") or {}
    header = ""
    if prog.get("total"):
        header = (
            f"**Progress:** {prog.get('done', 0)}/{prog['total']} done "
            f"({prog.get('percent', 0)}%) · {prog.get('pending', 0)} pending\n\n"
        )
    sections: list[str] = [header] if header else []
    if schedule_display and (schedule_display.get("days") or []):
        sections.append("Your tasks are in the **calendar panel** below.")
    else:
        task_text = format_goal_tasks(tasks)
        if task_text:
            sections.append(task_text)

    cal_lines = format_schedule_read_results(tool_results)
    if cal_lines:
        sections.append("")
        sections.append(f"**Existing calendar events ({start.isoformat()} → {end.isoformat()}):**")
        sections.append(cal_lines)

    free_data = None
    for r in tool_results:
        if r.get("tool") == "get_free_time" and r.get("ok"):
            free_data = r.get("data")
    free_text = format_free_time_summary(free_data)
    if free_text:
        sections.append("")
        sections.append(free_text)

    if not sections or (len(sections) == 1 and not sections[0].strip()):
        return (
            f"No tasks or calendar data for {start.isoformat()} → {end.isoformat()}. "
            "If you just created the plan, try refreshing the page."
        )
    return "\n".join(sections)


def format_tasks_summary_reply(
    *,
    tasks: list[dict[str, Any]],
    start: date,
    end: date,
    schedule_display: dict[str, Any] | None = None,
    all_tasks: list[dict[str, Any]] | None = None,
    scope: str = "week",
    day_label: str = "",
) -> str:
    """Reply for goal_tasks_summary — progress + brief task list only."""
    week_tasks = all_tasks if all_tasks is not None else tasks
    prog = (schedule_display or {}).get("progress") or {}
    header = ""
    if scope == "day" and tasks:
        header = format_progress_header(tasks, label=f"Progress ({day_label})" if day_label else "Progress")
    elif scope == "week" and prog.get("total"):
        header = (
            f"**Progress:** {prog.get('done', 0)}/{prog['total']} done "
            f"({prog.get('percent', 0)}%) · {prog.get('pending', 0)} pending"
        )
    elif scope == "week" and week_tasks:
        header = format_progress_header(week_tasks)

    if scope == "progress_only":
        if not prog.get("total") and week_tasks:
            prog = progress_counts(week_tasks)
        if prog.get("total"):
            return (
                f"**Progress:** {prog.get('done', 0)}/{prog['total']} done "
                f"({prog.get('percent', 0)}%) · {prog.get('pending', 0)} pending\n\n"
                f"Plan window: {start.isoformat()} → {end.isoformat()}. "
                "Ask **show my plan** or a day (e.g. **Wednesday tasks**) for the task list."
            )
        return "No tasks on this plan yet."

    if scope == "guidance":
        if not tasks:
            return (
                "Ask about a **specific task** or **day** (e.g. *help me with Wednesday's database task*) "
                "and I'll walk you through it."
            )
        title = "Guidance"
        if day_label:
            title = f"Guidance for {day_label}"
        elif len(tasks) == 1:
            title = f"Guidance: {(tasks[0].get('title') or 'Task').strip()}"
        body = format_goal_tasks_detail(tasks, title=title)
        body += (
            "\n\n_Ask a follow-up for more detail on any step. "
            "Say **show my plan** to see the full week._"
        )
        parts = [p for p in (body,) if p]
        return "\n\n".join(parts)

    if scope == "task_match":
        title = "Matching task" if len(tasks) == 1 else "Matching tasks"
        body = format_goal_tasks_detail(tasks, title=title)
        hint = (
            f"Plan window: {start.isoformat()} → {end.isoformat()}. "
            "Click **Preview** below the message for the full week calendar view."
        )
        parts = [p for p in (header, body, hint) if p]
        return "\n\n".join(parts)

    if scope == "day":
        if not tasks:
            return (
                f"No tasks found for **{day_label or 'that day'}** "
                f"(plan: {start.isoformat()} → {end.isoformat()})."
            )
        title = f"Tasks for {day_label}" if day_label else "Tasks for this day"
        body = format_goal_tasks_brief(tasks, title=title)
        hint = (
            f"Plan window: {start.isoformat()} → {end.isoformat()}. "
            "Click **Preview** below the message for the full week calendar view."
        )
        parts = [p for p in (header, body, hint) if p]
        return "\n\n".join(parts)

    week_tasks = all_tasks if all_tasks is not None else tasks
    body = format_plan_week_by_day(week_tasks, start=start, end=end)
    hint = (
        f"Plan window: {start.isoformat()} → {end.isoformat()}. "
        "Click **Preview** below the message for the full week calendar view."
    )
    parts = [p for p in (header, body, hint) if p]
    return "\n\n".join(parts)
