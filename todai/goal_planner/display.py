"""Format goal tasks + calendar for goal planner replies and UI schedule panel."""

from __future__ import annotations

from datetime import date, datetime
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
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calendar-style JSON for `renderScheduleDisplay` in the web UI."""
    by_date: dict[str, dict[str, Any]] = {}
    d = start
    while d <= end:
        by_date[d.isoformat()] = _empty_day_row(datetime.combine(d, datetime.min.time()))
        d = date.fromordinal(d.toordinal() + 1)

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
        by_date[iso]["slots"].append(
            {
                "time": when,
                "title": task_title,
                "description": desc,
                "status": st,
                "kind": "goal_task",
            }
        )

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
