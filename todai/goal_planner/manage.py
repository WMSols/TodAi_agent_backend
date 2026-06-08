"""Goal manage specialist — list/review/delete goals (tools + optional Groq reply)."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.core.groq_errors import is_groq_failure_reply
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.display import build_goal_plan_schedule_display
from todai.goal_planner.routing import groq_goal_manage_context
from todai.goal_planner.session_store import GoalPlanSessionStore
from todai.goal_planner.task_manage_query import DeleteManageIntent, parse_delete_manage_intent
from todai.goal_planner.task_query import _DELETE_VERB
from todai.goal_planner.turn_normalize import normalize_confirmation
from todai.goal_planner.tools import (
    execute_delete_all_goals,
    execute_delete_goal,
    execute_delete_plan,
    execute_list_goals_with_progress,
)

_DELETE_ALL_PATTERNS = re.compile(
    r"\b(delete|remove|clear)\b.*\b(all|every)\b.*\b(goal|plan)",
    re.I,
)

_MANAGE_SYSTEM = (
    "TodAI goal manage specialist. You receive TOOL_RESULTS JSON. "
    "Write a short, clear replyText in JSON: {\"replyText\": string}. "
    "Summarize goals, week plans, and progress (done/pending/total) from TOOL_RESULTS only. "
    "Never mention delete or ask for confirmation unless user_message explicitly requests deletion. "
    "No markdown code fences."
)


def _is_list_only_manage_turn(message: str, manage_action: str, pending: dict[str, Any]) -> bool:
    """List/review all goals — skip yes/no and delete-intent Groq when not deleting."""
    if pending:
        return False
    if manage_action != "list":
        return False
    return not re.search(r"\b(delete|remove|clear|drop)\b", message, re.I)


def _plan_window(
    store: GoalPlanSessionStore, plan_id: str
) -> tuple[date, date, list[dict[str, Any]]]:
    plan_row = store.get_plan_row(plan_id) or {}
    start = date.fromisoformat(str(plan_row.get("start_date", ""))[:10])
    end = date.fromisoformat(str(plan_row.get("end_date", ""))[:10])
    tasks = store.list_goal_tasks(plan_id)
    return start, end, tasks


def _schedule_patch(
    store: GoalPlanSessionStore, plan_id: str
) -> dict[str, Any]:
    plan_row = store.get_plan_row(plan_id)
    if not plan_row:
        return {}
    start = date.fromisoformat(str(plan_row["start_date"])[:10])
    end = date.fromisoformat(str(plan_row["end_date"])[:10])
    tasks = store.list_goal_tasks(plan_id)
    objective = str(plan_row.get("plan_notes") or "").strip()
    return {
        "schedule_display": build_goal_plan_schedule_display(
            tasks, start=start, end=end, goal_objective=objective
        ),
    }


def _format_delete_day_confirm(intent: DeleteManageIntent) -> str:
    lines = [
        f"This will **remove {len(intent.tasks)} task(s)** on **{intent.day_label}**:",
        "",
    ]
    for t in intent.tasks:
        lines.append(f"  • {(t.get('title') or 'Task').strip()}")
    lines.extend(
        [
            "",
            "Your goal and tasks on other days are **not** affected.",
            "Reply **yes** to confirm or **no** to cancel.",
        ]
    )
    return "\n".join(lines)


def _format_delete_task_confirm(intent: DeleteManageIntent) -> str:
    tasks = list(intent.tasks)
    if len(tasks) == 1:
        t = tasks[0]
        title = (t.get("title") or "Task").strip()
        d = str(t.get("task_date", ""))[:10]
        lines = [f"This will **remove one task**: **{title}**"]
        if d:
            lines.append(f"Scheduled: **{d}**")
    else:
        header = f"This will **remove {len(tasks)} task(s)**"
        if intent.day_label:
            header += f" on **{intent.day_label}**"
        header += ":"
        lines = [header, ""]
        for t in tasks:
            lines.append(f"  • {(t.get('title') or 'Task').strip()}")
        lines.append("")
        lines.append("Your goal and tasks on other days are **not** affected.")
    lines.extend(["", "Reply **yes** to confirm or **no** to cancel."])
    return "\n".join(lines)


def _confirm_context(pending: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_type": "delete_confirm",
        "pending_kind": pending.get("kind"),
        "day_label": pending.get("day_label"),
        "task_title": pending.get("task_title"),
        "task_count": len(pending.get("task_ids") or []),
    }


def _pending_from_intent(intent: DeleteManageIntent, plan_id: str) -> dict[str, Any]:
    task_ids = [str(t.get("id")) for t in intent.tasks if t.get("id")]
    base: dict[str, Any] = {
        "plan_id": plan_id,
        "task_ids": task_ids,
    }
    if intent.action == "delete_day":
        return {
            **base,
            "kind": "delete_day",
            "dates": list(intent.dates),
            "day_label": intent.day_label,
        }
    if intent.action == "delete_task":
        if len(intent.tasks) == 1:
            title = (intent.tasks[0].get("title") or "Task").strip()
        else:
            title = f"{len(intent.tasks)} tasks"
        return {**base, "kind": "delete_task", "task_title": title}
    return base


def _start_delete_flow(
    intent: DeleteManageIntent,
    *,
    plan_id: str,
    session: dict[str, Any],
    trace: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
    if intent.action == "clarify":
        return intent.clarify_message, {}, trace
    if intent.action == "delete_day" and intent.tasks:
        session["pending_manage"] = _pending_from_intent(intent, plan_id)
        trace.append({"phase": "pending_delete_day", "count": len(intent.tasks)})
        return (
            _format_delete_day_confirm(intent),
            {"pending_manage": session["pending_manage"]},
            trace,
        )
    if intent.action == "delete_task" and intent.tasks:
        pending = _pending_from_intent(intent, plan_id)
        session["pending_manage"] = pending
        trace.append(
            {
                "phase": "pending_delete_task",
                "count": len(intent.tasks),
                "task_ids": pending.get("task_ids"),
            }
        )
        return (
            _format_delete_task_confirm(intent),
            {"pending_manage": session["pending_manage"]},
            trace,
        )
    return None


def handle_goal_manage(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    manage_action: str,
    session: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
    router_tools: list[dict[str, Any]] | None = None,
    allow_groq: bool = True,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    tool_names = [t.get("tool") for t in (router_tools or []) if t.get("tool")]
    trace: list[dict[str, Any]] = [
        {"phase": "goal_manage", "action": manage_action, "router_tools": tool_names}
    ]
    pending = session.get("pending_manage") or {}

    if _is_list_only_manage_turn(message, manage_action, pending):
        trace.append({"phase": "confirm_normalize", "skipped": True, "reason": "list_only"})
        trace.append({"phase": "delete_intent", "skipped": True, "reason": "list_only"})
        return _handle_manage_list(
            store, plan_id, message, history=history, trace=trace
        )

    choice = "unclear"
    if pending:
        confirm_ctx = _confirm_context(pending)
        choice = normalize_confirmation(
            message, context=confirm_ctx, allow_groq=allow_groq
        ).choice
        trace.append({"phase": "confirm_normalize", "choice": choice, "pending": True})
    elif _DELETE_VERB.search(message or ""):
        trace.append(
            {"phase": "confirm_normalize", "skipped": True, "reason": "new_delete_request"}
        )
    else:
        choice = normalize_confirmation(
            message, context={"prompt_type": "manage"}, allow_groq=allow_groq
        ).choice
        trace.append({"phase": "confirm_normalize", "choice": choice, "pending": False})

    if pending.get("kind") == "delete_all" and choice == "yes":
        result = execute_delete_all_goals(store)
        session.pop("pending_manage", None)
        reply = (
            f"Removed **{result.get('goals_deleted', 0)}** goal(s), "
            f"**{result.get('plans_deleted', 0)}** plan(s), "
            f"**{result.get('tasks_deleted', 0)}** task(s)."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_all_goals", "result": result})
        return reply, {"phase": "interrogate", "answers": {}, "pending_manage": None}, trace

    if pending.get("kind") == "delete_goal" and choice == "yes":
        result = execute_delete_goal(store, plan_id)
        session.pop("pending_manage", None)
        reply = (
            f"Removed goal completely — **{result.get('goals_deleted', 0)}** goal, "
            f"**{result.get('plans_deleted', 0)}** plan(s), "
            f"**{result.get('tasks_deleted', 0)}** task(s). "
            "Pick another plan in the dropdown or create one under **New goal**."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_goal", "result": result})
        return (
            reply,
            {"phase": "interrogate", "answers": {}, "pending_manage": None, "goal_removed": True},
            trace,
        )

    if pending.get("kind") == "delete_plan" and choice == "yes":
        result = execute_delete_plan(store, plan_id)
        session.pop("pending_manage", None)
        reply = (
            f"Deleted **{result.get('tasks_deleted', 0)}** task(s) for this plan. "
            "You can start a new plan or answer the setup questions again."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_plan", "result": result})
        return reply, {"phase": "interrogate", "answers": {}, "pending_manage": None}, trace

    if pending.get("kind") == "delete_day" and choice == "yes":
        ids = list(pending.get("task_ids") or [])
        result = store.delete_goal_tasks_by_ids(ids, plan_id=plan_id)
        session.pop("pending_manage", None)
        label = pending.get("day_label") or "that day"
        reply = f"Removed **{result.get('count', 0)}** task(s) on **{label}**."
        trace.append({"phase": "goal_tool", "tool": "delete_day_tasks", "result": result})
        patch = {"pending_manage": None, **_schedule_patch(store, plan_id)}
        return reply, patch, trace

    if pending.get("kind") == "delete_task" and choice == "yes":
        ids = list(pending.get("task_ids") or [])
        result = store.delete_goal_tasks_by_ids(ids, plan_id=plan_id)
        session.pop("pending_manage", None)
        n = int(result.get("count") or len(ids) or 0)
        title = pending.get("task_title") or "task"
        reply = (
            f"Removed **{n}** task(s): **{title}**."
            if n != 1
            else f"Removed task **{title}**."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_task", "result": result})
        patch = {"pending_manage": None, **_schedule_patch(store, plan_id)}
        return reply, patch, trace

    if pending and choice == "no":
        session.pop("pending_manage", None)
        return "Cancelled — no changes made.", {"pending_manage": None}, trace

    if pending and choice == "unclear":
        return (
            "I'm not sure yet — reply **yes** to confirm the change or **no** to cancel.",
            {},
            trace,
        )

    start, end, all_tasks = _plan_window(store, plan_id)
    delete_intent = parse_delete_manage_intent(
        message, start=start, end=end, all_tasks=all_tasks, allow_groq=allow_groq
    )
    trace.append(
        {
            "phase": "delete_intent",
            "action": delete_intent.action,
            "dates": list(delete_intent.dates),
            "tasks": len(delete_intent.tasks),
        }
    )

    action = manage_action
    if action == "delete_plan" and delete_intent.action == "delete_day":
        started = _start_delete_flow(delete_intent, plan_id=plan_id, session=session, trace=trace)
        if started:
            return started

    if action in ("delete_day", "delete_task") and delete_intent.action in ("delete_day", "delete_task", "clarify"):
        started = _start_delete_flow(delete_intent, plan_id=plan_id, session=session, trace=trace)
        if started:
            return started

    if action == "none" and delete_intent.action in ("delete_day", "delete_task", "clarify"):
        started = _start_delete_flow(delete_intent, plan_id=plan_id, session=session, trace=trace)
        if started:
            return started

    if action == "none":
        if _DELETE_ALL_PATTERNS.search(message):
            action = "delete_all"
        elif delete_intent.action == "delete_all":
            action = "delete_all"
        elif delete_intent.action == "delete_plan":
            action = "delete_plan"
        elif delete_intent.action == "delete_goal":
            action = "delete_goal"
        elif delete_intent.action in ("delete_day", "delete_task"):
            started = _start_delete_flow(delete_intent, plan_id=plan_id, session=session, trace=trace)
            if started:
                return started
        elif delete_intent.action == "clarify":
            return delete_intent.clarify_message, {}, trace
        elif re.search(r"\b(delete|remove|clear|drop)\b", message, re.I):
            from todai.goal_planner.routing import match_operational_intent

            op = match_operational_intent(message)
            if op and op.manage_action in (
                "delete_goal",
                "delete_plan",
                "delete_all",
                "delete_day",
                "delete_task",
            ):
                action = op.manage_action
            else:
                action = "delete_goal"
        elif re.search(r"\b(list|review|show|progress|goals)\b", message, re.I):
            action = "list"

    if action in ("delete_day", "delete_task"):
        started = _start_delete_flow(delete_intent, plan_id=plan_id, session=session, trace=trace)
        if started:
            return started
        return (
            "I couldn't tell which tasks to remove. Try *remove Tuesday tasks* or "
            "*delete the first task on Friday*.",
            {},
            trace,
        )

    if not all_tasks and action in ("delete_plan", "delete_goal"):
        action = "delete_goal"
        trace.append({"phase": "delete_intent", "note": "empty_plan_upgrade_delete_goal"})

    if action == "delete_all":
        session["pending_manage"] = {"kind": "delete_all"}
        return (
            "This will **delete all goals, plans, and tasks** for your account. "
            "Reply **yes** to confirm or **no** to cancel.",
            {"pending_manage": session["pending_manage"]},
            trace,
        )

    if action == "delete_goal":
        session["pending_manage"] = {"kind": "delete_goal", "plan_id": plan_id}
        return (
            "This will **permanently delete everything for this goal**:\n"
            "• The goal record\n"
            "• This 7-day plan\n"
            "• **All** tasks (every day)\n"
            "• Chat history for this plan\n\n"
            "This cannot be undone. Reply **yes** to confirm or **no** to cancel.\n\n"
            "_To remove only one day, say **remove Tuesday tasks**. "
            "To clear the week but keep the goal, say **delete all plan tasks**._",
            {"pending_manage": session["pending_manage"]},
            trace,
        )

    if action == "delete_plan":
        session["pending_manage"] = {"kind": "delete_plan", "plan_id": plan_id}
        return (
            "This will **delete all tasks** for your current 7-day plan and reset it to **draft** "
            "(the goal itself stays). Reply **yes** to confirm or **no** to cancel.\n\n"
            "_To remove only one day, say **remove Tuesday tasks**._",
            {"pending_manage": session["pending_manage"]},
            trace,
        )

    if action == "edit":
        return (
            "To **change difficulty** or swap tasks for a day, say e.g. *make Tuesday easier* "
            "(coming soon). I can **explain how to do a task** — ask *help me with …* or "
            "*how do I … on Wednesday*. Use **show my plan** to review tasks.",
            {},
            trace,
        )

    return _handle_manage_list(
        store, plan_id, message, history=history, trace=trace, allow_groq=allow_groq
    )


def _handle_manage_list(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    trace: list[dict[str, Any]],
    allow_groq: bool = True,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """List all goals with progress — deterministic reply; Groq optional polish only."""
    data = execute_list_goals_with_progress(store, current_plan_id=plan_id)
    trace.append({"phase": "goal_tool", "tool": "list_goals_with_progress"})

    patch: dict[str, Any] = {}
    plan_row = store.get_plan_row(plan_id)
    if plan_row:
        start = date.fromisoformat(str(plan_row["start_date"])[:10])
        end = date.fromisoformat(str(plan_row["end_date"])[:10])
        tasks = store.list_goal_tasks(plan_id)
        objective = str(plan_row.get("plan_notes") or "").strip()
        display = build_goal_plan_schedule_display(
            tasks, start=start, end=end, goal_objective=objective
        )
        patch["schedule_display"] = display

    reply = _format_list_reply(data, plan_id=plan_id)
    # Keep list replies grounded in TOOL_RESULTS — Groq was inventing delete prompts.
    if (
        allow_groq
        and GROQ_API_KEY
        and data.get("plans")
        and re.search(r"\b(summarize|summary|overview|explain)\b", message, re.I)
    ):
        groq_reply = _groq_manage_reply(message, history, data)
        if groq_reply:
            reply = groq_reply

    return reply, patch, trace


def _format_list_reply(data: dict[str, Any], *, plan_id: str) -> str:
    lines = ["**Your goals & progress**", ""]
    goals = data.get("goals") or []
    if not goals:
        lines.append("No goals yet. Start a new 7-day plan above.")
        return "\n".join(lines)

    for g in goals[:10]:
        lines.append(
            f"• **{g.get('title', 'Goal')}** — {g.get('status', '?')} ({g.get('difficulty', 'medium')})"
        )

    lines.append("")
    lines.append("**Week plans**")
    for p in data.get("plans") or []:
        mark = " ← current" if str(p.get("id")) == plan_id else ""
        prog = p.get("progress") or {}
        lines.append(
            f"• {p.get('start_date')} → {p.get('end_date')} [{p.get('status', '?')}] "
            f"— {prog.get('done', 0)}/{prog.get('total', 0)} done ({prog.get('percent', 0)}%){mark}"
        )
    return "\n".join(lines)


def _groq_manage_reply(
    message: str,
    history: list[dict[str, Any]] | None,
    data: dict[str, Any],
) -> str | None:
    ctx = groq_goal_manage_context(history or [])
    payload = json.dumps({"user_message": message, "TOOL_RESULTS": data}, ensure_ascii=False)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _MANAGE_SYSTEM},
        *ctx,
        {"role": "user", "content": payload},
    ]
    try:
        raw = groq_chat_json(messages, phase="goal_manage", max_tokens=400, temperature=0.2)
        text = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
        if not text or is_groq_failure_reply(text):
            return None
        return text
    except Exception:
        return None
