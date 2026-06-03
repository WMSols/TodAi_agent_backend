"""
Goal plan HTTP service — start plan, process messages, read state.
"""

from __future__ import annotations

from typing import Any

from todai.database.buckets import goal_bucket_limits
from todai.database.config import use_local_storage
from todai.goal_planner.ai_intake import init_ai_intake
from todai.goal_planner.interrogation import answers_complete
from todai.goal_planner.orchestrator import orchestrate_goal_turn
from todai.goal_planner.plan_resolver import resolve_plan_for_turn
from todai.goal_planner.session_store import GoalPlanSessionStore
from todai.api.middleware.rate_limit import groq_tracker

GOAL_PLAN_DAYS = 7


def start_goal_plan(user_id: str, *, title: str, description: str = "") -> dict[str, Any]:
    store = GoalPlanSessionStore(user_id)
    out = store.create_plan(title=title, description=description)
    title_s = (title or "").strip()
    desc_s = (description or "").strip()
    intro, intake_patch = init_ai_intake(title_s, desc_s)
    session = store._load_plan_session(out["plan_id"]) or {}
    session.update(intake_patch)
    store._save_plan_session(out["plan_id"], session)
    if not use_local_storage():
        store.append_turn(
            out["plan_id"],
            user_message=description or title,
            assistant_message=intro,
            meta={"phase": "interrogate", "intake_style": "ai"},
        )
    return _goal_api_payload(
        out,
        reply_text=intro if not use_local_storage() else None,
        phase="interrogate",
        route="goal_interrogate",
        endpoint_phase="start",
    )


def _goal_api_payload(
    base: dict[str, Any],
    *,
    reply_text: str | None = None,
    phase: str = "intake",
    route: str = "goal_plan",
    endpoint_phase: str = "message",
    tool_trace: list[dict[str, Any]] | None = None,
    router_source: str | None = None,
    api_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape goal planner HTTP responses like calendar chat for UI + terminal logs."""
    out = dict(base)
    if reply_text:
        out["reply_text"] = reply_text
        out["assistant_text"] = reply_text
    out.setdefault("state", "idle")
    out["agent_mode"] = "goal_plan"
    out["last_agent_mode"] = "goal_plan"
    out["pipeline"] = "goal_planner"
    out["phase"] = phase
    planner = "goal_groq_router" if router_source in ("groq", "rules_mock") else "goal_rules_router"
    if router_source == "rules_fallback":
        planner = "goal_rules_router"
    if route == "goal_chat" and tool_trace:
        for step in tool_trace:
            if step.get("phase") == "goal_chat" and step.get("source") == "groq":
                planner = "goal_groq_chat"
                break
    out["debug"] = {
        "route": route,
        "intent": route,
        "phase": phase,
        "endpoint": endpoint_phase,
        "planner": planner,
        "router_source": router_source or "rules",
    }
    if tool_trace:
        out["tool_trace"] = tool_trace
    if api_usage:
        out["api_usage"] = api_usage
        if isinstance(out.get("debug"), dict):
            out["debug"]["api_usage"] = api_usage
    return out


def process_goal_plan_message(
    user_id: str,
    plan_id: str,
    message: str,
    *,
    ui_mode: str = "my_goals",
    storage_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One goal planning turn: router → interrogate / confirm / create / schedule read."""
    if use_local_storage():
        return _goal_api_payload(
            {
                "plan_id": plan_id,
                "reply_text": "Goal planner requires LOCAL=false and Supabase (run the SQL migration first).",
                "phase": "error",
                "messages": [],
            },
            reply_text="Goal planner requires LOCAL=false and Supabase (run the SQL migration first).",
            phase="error",
            route="goal_plan",
            endpoint_phase="message",
        )

    groq_tracker.begin_turn(user_id)
    store = GoalPlanSessionStore(user_id)
    hint_plan_id = (plan_id or "").strip()
    resolved_id, resolve_reason = resolve_plan_for_turn(store, message, hint_plan_id)
    plan_id = resolved_id
    if not plan_id:
        return _goal_api_payload(
            {"plan_id": "", "messages": []},
            reply_text="No goal plans yet. Use **New goal** to create one.",
            phase="error",
            route="goal_plan",
            endpoint_phase="message",
        )

    session = store._load_plan_session(plan_id)
    if not session:
        session = {"phase": "interrogate", "answers": {}}

    history = store.list_messages(plan_id)
    reply, patch, route, tool_trace = orchestrate_goal_turn(
        store, plan_id, message, history=history, ui_mode=ui_mode
    )
    router_source = None
    if tool_trace:
        first = tool_trace[0]
        if isinstance(first, dict):
            router_source = first.get("source")
    schedule_display = patch.pop("schedule_display", None)
    goal_removed = patch.pop("goal_removed", None)
    session.update(patch)
    phase = session.get("phase") or "interrogate"
    store._save_plan_session(plan_id, session)

    if reply:
        store.append_turn(
            plan_id,
            user_message=message,
            assistant_message=reply,
            meta={"phase": phase, "route": route},
        )

    out_debug = {
        "resolve_reason": resolve_reason,
        "hint_plan_id": hint_plan_id or None,
    }
    payload = _goal_api_payload(
        {
            "plan_id": plan_id,
            "resolved_plan_id": plan_id,
            "plan_resolved": plan_id != hint_plan_id,
            "messages": store.list_messages(plan_id),
            "history_pull": goal_bucket_limits().pull,
            "answers_complete": answers_complete(session.get("answers") or {}),
            "schedule_display": schedule_display,
            "goal_removed": goal_removed,
        },
        reply_text=reply or None,
        phase=phase,
        route=route,
        endpoint_phase="message",
        tool_trace=tool_trace,
        router_source=router_source,
    )
    usage = groq_tracker.usage_snapshot(user_id)
    if isinstance(payload.get("debug"), dict):
        payload["debug"].update(out_debug)
        payload["debug"]["ui_mode"] = ui_mode
    payload["api_usage"] = usage
    if isinstance(payload.get("debug"), dict):
        payload["debug"]["api_usage"] = usage
    return payload


def list_goal_plans(user_id: str) -> dict[str, Any]:
    """List user's week plans with progress (for UI plan picker)."""
    if use_local_storage():
        return _goal_api_payload(
            {"plans": [], "goals": []},
            phase="error",
            route="goal_manage",
            endpoint_phase="plans",
        )

    from todai.goal_planner.tools import execute_list_goals_with_progress

    store = GoalPlanSessionStore(user_id)
    data = execute_list_goals_with_progress(store)
    return _goal_api_payload(
        {
            "plans": data.get("plans") or [],
            "goals": data.get("goals") or [],
        },
        phase="list",
        route="goal_manage",
        endpoint_phase="plans",
    )


def get_goal_plan_state(
    user_id: str,
    plan_id: str,
    *,
    include_messages: bool = True,
) -> dict[str, Any]:
    from datetime import date

    from todai.goal_planner.display import build_goal_plan_schedule_display

    store = GoalPlanSessionStore(user_id)
    session = store._load_plan_session(plan_id)
    phase = session.get("phase") or "intake"
    schedule_display = None
    plan_row = store.get_plan_row(plan_id)
    if plan_row and phase == "active":
        start = date.fromisoformat(str(plan_row["start_date"])[:10])
        end = date.fromisoformat(str(plan_row["end_date"])[:10])
        tasks = store.list_goal_tasks(plan_id)
        schedule_display = build_goal_plan_schedule_display(tasks, start=start, end=end)
    messages = store.list_messages(plan_id) if include_messages else []
    payload = _goal_api_payload(
        {
            "plan_id": plan_id,
            "session": session,
            "messages": messages,
            "bucket_limits": {
                "store": goal_bucket_limits().store,
                "pull": goal_bucket_limits().pull,
            },
            "schedule_display": schedule_display if include_messages else None,
        },
        phase=phase,
        endpoint_phase="state",
    )
    usage = groq_tracker.usage_snapshot(user_id)
    payload["api_usage"] = usage
    if isinstance(payload.get("debug"), dict):
        payload["debug"]["api_usage"] = usage
    return payload
