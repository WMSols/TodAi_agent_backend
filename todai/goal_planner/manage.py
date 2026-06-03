"""Goal manage specialist — list/review/delete goals (tools + optional Groq reply)."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.display import build_goal_plan_schedule_display
from todai.goal_planner.interrogation import parse_confirmation
from todai.goal_planner.routing.context import groq_goal_manage_context
from todai.goal_planner.session_store import GoalPlanSessionStore
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
    "Summarize goals, week plans, and progress (done/pending/total). "
    "If delete was requested but needs confirmation, say so. No markdown code fences."
)


def handle_goal_manage(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    manage_action: str,
    session: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
    router_tools: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    tool_names = [t.get("tool") for t in (router_tools or []) if t.get("tool")]
    trace: list[dict[str, Any]] = [
        {"phase": "goal_manage", "action": manage_action, "router_tools": tool_names}
    ]
    pending = session.get("pending_manage") or {}

    if pending.get("kind") == "delete_all" and parse_confirmation(message) == "yes":
        result = execute_delete_all_goals(store)
        session.pop("pending_manage", None)
        reply = (
            f"Removed **{result.get('goals_deleted', 0)}** goal(s), "
            f"**{result.get('plans_deleted', 0)}** plan(s), "
            f"**{result.get('tasks_deleted', 0)}** task(s)."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_all_goals", "result": result})
        return reply, {"phase": "interrogate", "answers": {}, "pending_manage": None}, trace

    if pending.get("kind") == "delete_goal" and parse_confirmation(message) == "yes":
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

    if pending.get("kind") == "delete_plan" and parse_confirmation(message) == "yes":
        result = execute_delete_plan(store, plan_id)
        session.pop("pending_manage", None)
        reply = (
            f"Deleted **{result.get('tasks_deleted', 0)}** task(s) for this plan. "
            "You can start a new plan or answer the setup questions again."
        )
        trace.append({"phase": "goal_tool", "tool": "delete_plan", "result": result})
        return reply, {"phase": "interrogate", "answers": {}, "pending_manage": None}, trace

    if pending and parse_confirmation(message) == "no":
        session.pop("pending_manage", None)
        return "Cancelled — no changes made.", {"pending_manage": None}, trace

    action = manage_action
    if action == "none":
        if _DELETE_ALL_PATTERNS.search(message):
            action = "delete_all"
        elif re.search(r"\b(delete|remove|clear|drop)\b", message, re.I):
            from todai.goal_planner.routing.rules_router import match_operational_intent

            op = match_operational_intent(message)
            if op and op.manage_action in ("delete_goal", "delete_plan", "delete_all"):
                action = op.manage_action
            else:
                action = "delete_goal"
        elif re.search(r"\b(list|review|show|progress|goals)\b", message, re.I):
            action = "list"

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
            "This will **permanently remove this goal** (goal record, week plan, tasks, and chat). "
            "Reply **yes** to confirm or **no** to cancel.\n\n"
            "_To only clear tasks and keep the goal, say **delete plan tasks**._",
            {"pending_manage": session["pending_manage"]},
            trace,
        )

    if action == "delete_plan":
        session["pending_manage"] = {"kind": "delete_plan", "plan_id": plan_id}
        return (
            "This will **delete all tasks** for your current 7-day plan and reset it to **draft** "
            "(the goal itself stays). Reply **yes** to confirm or **no** to cancel.",
            {"pending_manage": session["pending_manage"]},
            trace,
        )

    if action == "edit":
        return (
            "Task editing (move, mark done) is coming soon. Use **show my plan** to review tasks, "
            "or **delete my goals** to remove this plan.",
            {},
            trace,
        )

    # Default: list / review with full progress
    data = execute_list_goals_with_progress(store, current_plan_id=plan_id)
    trace.append({"phase": "goal_tool", "tool": "list_goals_with_progress"})

    patch: dict[str, Any] = {}
    plan_row = store.get_plan_row(plan_id)
    if plan_row:
        start = date.fromisoformat(str(plan_row["start_date"])[:10])
        end = date.fromisoformat(str(plan_row["end_date"])[:10])
        tasks = store.list_goal_tasks(plan_id)
        display = build_goal_plan_schedule_display(tasks, start=start, end=end)
        patch["schedule_display"] = display

    reply = _format_list_reply(data, plan_id=plan_id)
    if GROQ_API_KEY and data.get("plans"):
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
        text = raw.get("replyText") or raw.get("reply_text") or ""
        return str(text).strip() or None
    except Exception:
        return None
