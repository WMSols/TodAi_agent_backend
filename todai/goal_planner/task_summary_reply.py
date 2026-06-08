"""Natural-language replies for goal_tasks_summary (Groq + structured task data)."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from todai.agent.core.groq_errors import is_groq_failure_reply
from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.display import format_tasks_summary_reply, progress_counts
from todai.goal_planner.routing import groq_goal_manage_context
from todai.goal_planner.task_query import TaskSummaryQuery

logger = logging.getLogger(__name__)

_TASK_SUMMARY_SYSTEM = (
    "You are TodAI's goal-plan task specialist \n"
    "You get the user's message and TOOL_RESULTS JSON (real tasks from the database).\n"
    "Reply with JSON only: {\"replyText\": string}\n\n"
    "Rules:\n"
    "- Answer **exactly** what user asked in terms of guidance, clarity, help tips, summarizing, or analyzing.\n"
    "- **DO NOT INVENT ANYTHING.** Use only data in TOOL_RESULTS — never guess tasks, days, dates, or progress.\n"
    "- Use **only** tasks in TOOL_RESULTS.tasks. Never invent, remove, rename, or move tasks to another day.\n"
    "- For scope=week use TOOL_RESULTS.tasks_by_date: each YYYY-MM-DD key lists that day's tasks only. "
    "If a day key has an empty list, say **No tasks scheduled** for that day — never fill it in.\n"
    "- Never list a task on a weekday unless its task_date in TOOL_RESULTS matches that calendar day.\n"
    "- scope=day → only that day; scope=task_match → only matched tasks; "
    "scope=progress_only → progress summary, no full task dump.\n"
    "- scope=day + casual/greeting message → open with TOOL_RESULTS.server_today.label, "
    "then today's tasks; optionally one line of week progress from TOOL_RESULTS.progress.week.\n"
    "- scope=guidance → brief coaching from task title+description only — do not add steps not in the data.\n"
    "- For hardest days / general advice without a task list → user should use goal chat.\n"
    "- scope=week → list every plan day from tasks_by_date in order; empty days = no tasks scheduled.\n"
    "- Include progress from TOOL_RESULTS.progress when they ask about progress.\n"
    "- Do not claim tasks were changed, moved, or deleted.\n"
    "- For today's date/weekday use ONLY TOOL_RESULTS.server_today — never guess.\n"
    "No markdown code fences inside JSON."
)


def _task_time_label(row: dict[str, Any]) -> str:
    st, en = row.get("start_time"), row.get("end_time")
    if st and en:
        return f"{st} – {en}"
    return "flexible"


def _build_tasks_by_date(
    tasks: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    """One entry per plan day; days with no DB tasks map to []."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        d = str(t.get("task_date", ""))[:10]
        by_date.setdefault(d, []).append(t)
    out: dict[str, list[dict[str, Any]]] = {}
    cur = start
    while cur <= end:
        iso = cur.isoformat()
        out[iso] = _compact_tasks(by_date.get(iso, []))
        cur += timedelta(days=1)
    return out


def _compact_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tasks:
        out.append(
            {
                "id": str(t.get("id") or ""),
                "title": (t.get("title") or "Task").strip(),
                "description": (t.get("description") or "").strip(),
                "task_date": str(t.get("task_date", ""))[:10],
                "time": _task_time_label(t),
                "status": (t.get("status") or "pending").lower(),
            }
        )
    return out


def build_task_summary_tool_results(
    *,
    message: str,
    query: TaskSummaryQuery,
    view_tasks: list[dict[str, Any]],
    all_tasks: list[dict[str, Any]],
    start: date,
    end: date,
    goal_title: str = "",
    objective: str = "",
    server_today: dict[str, str] | None = None,
) -> dict[str, Any]:
    week_prog = progress_counts(all_tasks)
    filtered_prog = progress_counts(view_tasks) if view_tasks else None
    payload: dict[str, Any] = {
        "ok": True,
        "user_message": (message or "").strip(),
        "scope": query.scope,
        "day_label": query.day_label,
        "dates": list(query.dates),
        "plan": {
            "goal_title": goal_title,
            "objective": objective,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        "progress": {
            "week": week_prog,
            "filtered": filtered_prog,
        },
        "tasks": _compact_tasks(view_tasks),
        "task_count": len(view_tasks),
        "week_task_count": len(all_tasks),
    }
    payload["server_today"] = server_today or {}
    if query.scope == "week":
        payload["tasks_by_date"] = _build_tasks_by_date(all_tasks, start=start, end=end)
    return payload


def _groq_task_summary_reply(
    message: str,
    history: list[dict[str, Any]] | None,
    tool_results: dict[str, Any],
) -> str | None:
    if not GROQ_API_KEY:
        return None
    ctx = groq_goal_manage_context(history or [])
    payload = json.dumps(
        {"user_message": message, "TOOL_RESULTS": tool_results},
        ensure_ascii=False,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _TASK_SUMMARY_SYSTEM},
        *ctx,
        {"role": "user", "content": payload},
    ]
    scope = tool_results.get("scope") or "week"
    max_tokens = 550 if scope in ("day", "progress_only", "task_match", "guidance") else 900
    try:
        raw = groq_chat_json(
            messages,
            phase="goal_tasks_summary",
            max_tokens=max_tokens,
            temperature=0.35,
        )
        text = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
        if not text or is_groq_failure_reply(text):
            return None
        return text
    except Exception as e:
        logger.warning("goal task summary Groq failed: %s", e)
        return None


def compose_task_summary_reply(
    *,
    message: str,
    history: list[dict[str, Any]] | None,
    query: TaskSummaryQuery,
    view_tasks: list[dict[str, Any]],
    all_tasks: list[dict[str, Any]],
    start: date,
    end: date,
    schedule_display: dict[str, Any] | None,
    goal_title: str = "",
    objective: str = "",
    server_today: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    Returns (reply_text, source) where source is groq | template.
    """
    tool_results = build_task_summary_tool_results(
        message=message,
        query=query,
        view_tasks=view_tasks,
        all_tasks=all_tasks,
        start=start,
        end=end,
        goal_title=goal_title,
        objective=objective,
        server_today=server_today,
    )
    if query.scope == "week":
        template = format_tasks_summary_reply(
            tasks=view_tasks,
            start=start,
            end=end,
            schedule_display=schedule_display,
            all_tasks=all_tasks,
            scope=query.scope,
            day_label=query.day_label,
        )
        return template, "template"

    groq_reply = _groq_task_summary_reply(message, history, tool_results)
    if groq_reply:
        return groq_reply, "groq"

    template = format_tasks_summary_reply(
        tasks=view_tasks,
        start=start,
        end=end,
        schedule_display=schedule_display,
        all_tasks=all_tasks,
        scope=query.scope,
        day_label=query.day_label,
    )
    return template, "template"
