"""
Goal plan HTTP service — start plan, process messages, read state.
"""

from __future__ import annotations

from typing import Any

from todai.database.buckets import goal_bucket_limits
from todai.database.config import use_local_storage
from todai.goal_planner.interrogation import QUESTIONS, answers_complete
from todai.goal_planner.orchestrator import orchestrate_goal_turn
from todai.goal_planner.session_store import GoalPlanSessionStore

GOAL_PLAN_DAYS = 7


def start_goal_plan(user_id: str, *, title: str, description: str = "") -> dict[str, Any]:
    store = GoalPlanSessionStore(user_id)
    out = store.create_plan(title=title, description=description)
    title_s = (title or "").strip()
    intro = QUESTIONS["objective"]
    if title_s:
        intro = (
            f"**7-day plan:** {title_s}\n\n"
            f"{QUESTIONS['objective']}\n\n"
            "_Tip: reply **ok** to use this title as your objective, or write your own._"
        )
    if not use_local_storage():
        store.append_turn(
            out["plan_id"],
            user_message=description or title,
            assistant_message=intro,
            meta={"phase": "interrogate", "step": "objective"},
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
    out["debug"] = {
        "route": route,
        "intent": route,
        "phase": phase,
        "endpoint": endpoint_phase,
        "planner": "goal_rules",
    }
    if tool_trace:
        out["tool_trace"] = tool_trace
    return out


def process_goal_plan_message(
    user_id: str,
    plan_id: str,
    message: str,
    *,
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

    store = GoalPlanSessionStore(user_id)
    session = store._load_plan_session(plan_id)
    if not session:
        return _goal_api_payload(
            {"plan_id": plan_id, "messages": []},
            reply_text="Plan session not found. Start a new plan from the Goal planner panel.",
            phase="error",
            route="goal_plan",
            endpoint_phase="message",
        )

    reply, patch, route, tool_trace = orchestrate_goal_turn(store, plan_id, message)
    schedule_display = patch.pop("schedule_display", None)
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

    return _goal_api_payload(
        {
            "plan_id": plan_id,
            "messages": store.list_messages(plan_id),
            "history_pull": goal_bucket_limits().pull,
            "answers_complete": answers_complete(session.get("answers") or {}),
            "schedule_display": schedule_display,
        },
        reply_text=reply or None,
        phase=phase,
        route=route,
        endpoint_phase="message",
        tool_trace=tool_trace,
    )


def get_goal_plan_state(user_id: str, plan_id: str) -> dict[str, Any]:
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
    return _goal_api_payload(
        {
            "plan_id": plan_id,
            "session": session,
            "messages": store.list_messages(plan_id),
            "bucket_limits": {
                "store": goal_bucket_limits().store,
                "pull": goal_bucket_limits().pull,
            },
            "schedule_display": schedule_display,
        },
        phase=phase,
        endpoint_phase="state",
    )
