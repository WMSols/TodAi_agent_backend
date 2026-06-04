"""

Goal plan HTTP service — start plan, process messages, read state.

"""



from __future__ import annotations



from typing import Any



from todai.database.buckets import goal_bucket_limits

from todai.goal_planner.ai_intake import init_ai_intake

from todai.goal_planner.interrogation import answers_complete

from todai.goal_planner.orchestrator import orchestrate_goal_turn

from todai.goal_planner.plan_resolver import resolve_plan_for_turn

from todai.goal_planner.session_store import GoalPlanSessionStore

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.api.middleware.rate_limit import groq_tracker



GOAL_PLAN_DAYS = 7


def _goal_groq_allowed(
    user_id: str,
    *,
    planned_requests: int = 2,
    planned_tokens: int = 2500,
) -> tuple[bool, dict[str, Any]]:
    """Calendar-style pre-flight: skip Groq HTTP when local/org limits block the turn."""
    if not GROQ_API_KEY:
        return True, {}
    gate = groq_tracker.check_turn_allowed(
        planned_requests=planned_requests,
        planned_tokens=planned_tokens,
    )
    if gate.allowed:
        return True, {}
    usage = groq_tracker.usage_snapshot(user_id)
    usage.update(gate.to_usage_extra())
    return False, usage


def start_goal_plan(

    user_id: str,

    *,

    achievement: str = "",

    title: str = "",

    description: str = "",

) -> dict[str, Any]:

    achievement_s = (achievement or "").strip() or (description or "").strip() or (title or "").strip()

    if not achievement_s:

        raise ValueError("achievement is required")



    store = GoalPlanSessionStore(user_id)

    out = store.create_plan(title="New goal", description=achievement_s)

    groq_tracker.begin_turn(user_id)
    allow_groq, _ = _goal_groq_allowed(user_id, planned_requests=1, planned_tokens=800)

    intro, intake_patch = init_ai_intake(achievement_s, allow_groq=allow_groq)
    if not allow_groq:
        groq_tracker.mark_preflight_only_turn(user_id)

    generated_title = (

        intake_patch.pop("generated_goal_title", None)

        or intake_patch.get("title")

        or "New goal"

    )

    user_notes = (intake_patch.get("description") or achievement_s).strip()

    goal_id = out.get("goal_id")

    if goal_id:

        store.update_goal(

            str(goal_id),

            title=str(generated_title).strip()[:200],

            description=user_notes,

        )

        out["title"] = generated_title

        out["description"] = user_notes



    session = store._load_plan_session(out["plan_id"]) or {}

    session.update(intake_patch)

    session["achievement"] = achievement_s

    session["title"] = generated_title

    session["description"] = user_notes

    store._save_plan_session(out["plan_id"], session)

    store.append_turn(

        out["plan_id"],

        user_message=achievement_s,

        assistant_message=intro,

        meta={"phase": "interrogate", "intake_style": "ai"},

    )

    usage = groq_tracker.usage_snapshot(user_id)

    return _goal_api_payload(

        out,

        reply_text=intro,

        phase="interrogate",

        route="goal_interrogate",

        endpoint_phase="start",

        router_source="rules_fallback" if not allow_groq else None,

        api_usage=usage,

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

    groq_tracker.begin_turn(user_id)
    allow_groq, preflight_usage = _goal_groq_allowed(user_id)

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

        store,
        plan_id,
        message,
        history=history,
        ui_mode=ui_mode,
        allow_groq=allow_groq,

    )

    if not allow_groq:
        groq_tracker.mark_preflight_only_turn(user_id)

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
    if preflight_usage:
        usage = {**usage, **preflight_usage}

    if isinstance(payload.get("debug"), dict):

        payload["debug"].update(out_debug)

        payload["debug"]["ui_mode"] = ui_mode

    payload["api_usage"] = usage

    if isinstance(payload.get("debug"), dict):

        payload["debug"]["api_usage"] = usage

    return payload





def list_goal_plans(user_id: str) -> dict[str, Any]:

    """List user's week plans with progress (for UI plan picker)."""

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


