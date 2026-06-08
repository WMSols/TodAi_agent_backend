"""Goal planner turn orchestration: router → handlers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from todai.database import user_store
from todai.goal_planner.create import build_tasks_from_free_time, fetch_plan_window_schedule
from todai.goal_planner.display import (
    build_goal_plan_schedule_display,
    format_goal_tasks,
    format_plan_schedule_reply,
)
from todai.goal_planner.task_generator import enrich_tasks_with_descriptions
from todai.goal_planner.chat import handle_goal_chat
from todai.goal_planner.manage import handle_goal_manage
from todai.goal_planner.router import route_goal_turn
from todai.goal_planner.interrogation import (
    STEPS,
    _answer_label,
    answers_complete,
    ensure_plan_defaults,
    format_skip_days,
    next_missing_step,
    plan_difficulty,
    plan_minutes_per_day,
    plan_skip_days,
    question_for_step,
)
from todai.goal_planner.ai_intake import handle_ai_confirm, handle_ai_intake_turn, uses_ai_intake
from todai.goal_planner.plan_resolver import plan_needs_task_setup
from todai.goal_planner.session_store import GoalPlanSessionStore
from todai.goal_planner.task_query import filter_tasks_by_dates, parse_task_summary_query
from todai.goal_planner.task_summary_reply import compose_task_summary_reply

from todai.agent.tools.calendar import execute_read_tools
from todai.goal_planner.routing import normalize_router_tools
from todai.goal_planner.routing.router import should_hold_pending_manage

UiMode = str  # "my_goals" | "new_goal"


def orchestrate_goal_turn(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    ui_mode: str = "my_goals",
    allow_groq: bool = True,
) -> tuple[str, dict[str, Any], str, list[dict[str, Any]]]:
    """
    Returns (reply, session_patch, route, tool_trace).
    Caller merges session_patch and persists messages.
    """
    session = store._load_plan_session(plan_id)
    if not session:
        session = {"phase": "interrogate", "answers": {}}

    from todai.goal_planner.today_context import get_server_today_for_user

    session["server_today"] = get_server_today_for_user(store.api_user_id)

    answers = session.setdefault("answers", {})
    phase = session.get("phase") or "interrogate"
    intake_mode = ui_mode == "new_goal"
    needs_setup = plan_needs_task_setup(store, plan_id, session)
    _hydrate_session_goal_fields(store, plan_id, session)
    if intake_mode and needs_setup and session.get("intake_style") != "ai":
        session["intake_style"] = "ai"
    route_out = route_goal_turn(
        message=message,
        phase=phase,
        answers=answers,
        plan_id=plan_id,
        session=session,
        history=history,
        ui_mode=ui_mode,
        needs_task_setup=needs_setup,
        allow_groq=allow_groq,
    )
    pending_reroute = False
    if (
        session.get("pending_manage")
        and route_out.route == "goal_manage"
        and not should_hold_pending_manage(message)
    ):
        session.pop("pending_manage", None)
        route_out = route_goal_turn(
            message=message,
            phase=phase,
            answers=answers,
            plan_id=plan_id,
            session=session,
            history=history,
            ui_mode=ui_mode,
            needs_task_setup=needs_setup,
            allow_groq=allow_groq,
        )
        pending_reroute = True
    setup_mode = intake_mode
    route = route_out.route
    manage_action = route_out.manage_action
    router_tools = list(route_out.tools)

    trace: list[dict[str, Any]] = [
        {
            "phase": "goal_router",
            "route": route_out.route,
            "final_route": route,
            "manage_action": manage_action,
            "tools": [t.get("tool") for t in router_tools if t.get("tool")],
            "reason": route_out.reason,
            "source": route_out.source,
            "ui_mode": ui_mode,
        }
    ]
    trace.extend(route_out.guard_notes)

    if pending_reroute:
        trace.append({"phase": "pending_cleared", "reason": "reroute", "route": route})
    elif (
        session.get("pending_manage")
        and route != "goal_manage"
        and not should_hold_pending_manage(message)
    ):
        session.pop("pending_manage", None)
        trace.append({"phase": "pending_cleared", "reason": "new_intent", "route": route})

    if not intake_mode and needs_setup and route in ("goal_interrogate", "goal_confirm", "goal_create"):
        reply = (
            "This plan doesn't have tasks yet. Open the **New goal** tab to finish AI setup "
            "(title + description → tailored questions → build 7-day tasks)."
        )
        trace.append({"phase": "route_hint", "reason": "setup_on_new_goal_tab"})
        return reply, {}, "goal_chat", trace

    if route == "goal_interrogate" and setup_mode:
        if not uses_ai_intake(session, ui_mode):
            trace.append({"phase": "legacy_static_intake_blocked", "intake_style": session.get("intake_style")})
            return (
                "This plan needs the **New goal** tab AI setup (not the old fixed 3-question wizard). "
                "Open **New goal** and continue from there.",
                {},
                "goal_chat",
                trace,
            )
        reply, patch, intake_meta = handle_ai_intake_turn(
            session, message, allow_groq=allow_groq
        )
        session.update(patch)
        trace.append(
            {
                "phase": "goal_ai_intake",
                "intake_style": "ai",
                **intake_meta,
            }
        )
        return reply, patch, route, trace

    if route == "goal_confirm" and setup_mode:
        if not uses_ai_intake(session, ui_mode):
            trace.append({"phase": "legacy_static_confirm_blocked", "intake_style": session.get("intake_style")})
            return (
                "Please finish setup on the **New goal** tab (AI intake + review), then reply **yes** to build tasks.",
                {},
                "goal_chat",
                trace,
            )
        reply, patch = handle_ai_confirm(session, message, allow_groq=allow_groq)
        session.update(patch)
        trace.append({"phase": "goal_ai_intake", "step": "confirm"})
        if patch.get("phase") == "ready":
            reply, patch, create_trace = _handle_create(store, plan_id, {**session, **patch})
            trace.extend(create_trace)
            return reply, patch, "goal_create", trace
        return reply, patch, route, trace

    if route == "goal_create" and setup_mode:
        reply, patch, create_trace = _handle_create(store, plan_id, session)
        trace.extend(create_trace)
        return reply, patch, route, trace

    if route == "goal_tasks_summary":
        reply, patch, sum_trace = _handle_tasks_summary(
            store,
            plan_id,
            message,
            history=history,
            server_today=session.get("server_today"),
        )
        trace.extend(sum_trace)
        return reply, patch, route, trace

    if route == "goal_schedule_read":
        reply, patch, read_trace = _handle_schedule_read(
            store, plan_id, session, message, router_tools=router_tools
        )
        trace.extend(read_trace)
        return reply, patch, route, trace

    if route == "goal_manage":
        reply, patch, manage_trace = handle_goal_manage(
            store,
            plan_id,
            message,
            manage_action=manage_action,
            session=session,
            history=history,
            router_tools=router_tools,
            allow_groq=allow_groq,
        )
        trace.extend(manage_trace)
        return reply, patch, route, trace

    if route == "goal_chat":
        reply, patch, chat_trace = handle_goal_chat(
            store,
            plan_id,
            message,
            session=session,
            phase=phase,
            history=history,
            ui_mode=ui_mode,
            needs_task_setup=needs_setup,
        )
        trace.extend(chat_trace)
        return reply, patch, route, trace

    if phase == "creating":
        return (
            "Your plan is being built — give me a moment, then ask **show my plan**.",
            {},
            route,
            trace,
        )

    reply = (
        "Use the **New goal** tab to describe what you want — I'll ask tailored questions, "
        "then build a 7-day task plan."
    )
    return reply, {}, "goal_chat", trace


def _hydrate_session_goal_fields(
    store: GoalPlanSessionStore, plan_id: str, session: dict[str, Any]
) -> None:
    if session.get("title") and session.get("description") is not None:
        return
    row = store.get_plan_row(plan_id) or {}
    gid = str(row.get("goal_id") or session.get("goal_id") or "")
    for g in store.list_user_goals():
        if str(g.get("id")) == gid:
            session.setdefault("title", (g.get("title") or "").strip())
            session.setdefault("description", (g.get("description") or "").strip())
            break


def _seed_intake_from_goal(session: dict[str, Any]) -> dict[str, Any]:
    """Pre-fill objective from goal title/description when starting task setup."""
    answers = session.setdefault("answers", {})
    if answers.get("objective", {}).get("valid"):
        return {}
    title = (session.get("title") or "").strip()
    desc = (session.get("description") or "").strip()
    obj = title
    if title and desc:
        obj = f"{title} — {desc}" if len(desc) < 120 else f"{title} — {desc[:117]}…"
    elif desc:
        obj = desc
    if len(obj) < 3:
        return {}
    answers["objective"] = {
        "valid": True,
        "parsed": obj[:500],
        "raw": obj,
        "display": obj[:80],
    }
    nxt = next_missing_step(answers) or "difficulty"
    return {"answers": answers, "intake_step": nxt, "phase": "interrogate"}


def _handle_create(
    store: GoalPlanSessionStore,
    plan_id: str,
    session: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    answers = ensure_plan_defaults(session.get("answers") or {})
    if not answers_complete(answers):
        step = next_missing_step(answers) or STEPS[0]
        return (
            f"I still need a valid answer for **{step.replace('_', ' ')}**.\n\n{question_for_step(step, session)}",
            {"phase": "interrogate", "intake_step": step},
            trace,
        )

    plan_row = store.get_plan_row(plan_id)
    if not plan_row:
        return "Plan not found.", {"phase": "error"}, trace

    start = date.fromisoformat(str(plan_row["start_date"])[:10])
    end = date.fromisoformat(str(plan_row["end_date"])[:10])
    goal_id = str(plan_row["goal_id"])
    objective = str(answers["objective"]["parsed"])
    difficulty = plan_difficulty(answers)
    tasks_per_day = int(answers["tasks_per_day"]["parsed"])
    minutes_per_day = plan_minutes_per_day(answers)
    skip_days = plan_skip_days(answers)

    session["phase"] = "creating"
    store._save_plan_session(plan_id, session)

    tool_results = fetch_plan_window_schedule(store.api_user_id, start, end)
    trace.append({"phase": "prefetch", "calls": ["get_free_time", "get_schedule_range"]})

    free_data: dict[str, Any] = {}
    for r in tool_results:
        if r.get("tool") == "get_free_time" and r.get("ok"):
            free_data = r.get("data") or {}

    days_count = (end - start).days + 1
    task_rows = build_tasks_from_free_time(
        objective=objective,
        difficulty=difficulty,
        tasks_per_day=tasks_per_day,
        minutes_per_day=minutes_per_day,
        start=start,
        days=days_count,
        free_time_data=free_data,
        skip_days=skip_days,
    )
    task_rows, gen_err = enrich_tasks_with_descriptions(
        objective=objective,
        difficulty=difficulty,
        tasks=task_rows,
        minutes_per_day=minutes_per_day,
        tasks_per_day=tasks_per_day,
        plan_start=start,
    )
    if gen_err:
        session["phase"] = "confirm"
        store._save_plan_session(plan_id, session)
        usage = None
        try:
            from todai.api.middleware.rate_limit import groq_tracker

            usage = groq_tracker.usage_snapshot(store.api_user_id)
        except Exception:
            pass
        trace.append(
            {
                "phase": "goal_task_gen_failed",
                "code": gen_err.code,
                "limit_hit": gen_err.limit_hit,
            }
        )
        return gen_err.user_reply(usage), {"phase": "confirm", "answers": answers}, trace

    for row in task_rows:
        row.pop("_day_index", None)
        row.pop("_task_num", None)

    inserted = store.insert_goal_tasks(plan_id, goal_id, task_rows)
    store.update_plan_after_create(
        plan_id,
        goal_id,
        difficulty=difficulty,
        plan_notes=objective,
    )

    session["phase"] = "active"
    session["tasks_created"] = inserted
    store._save_plan_session(plan_id, session)

    tasks = store.list_goal_tasks(plan_id)
    display = build_goal_plan_schedule_display(
        tasks,
        start=start,
        end=end,
        title=f"Goal plan: {objective[:50]}",
        goal_objective=objective,
        tool_results=tool_results,
    )
    prog = display.get("progress") or {}
    active_days = days_count - sum(
        1 for i in range(days_count) if (start + timedelta(days=i)).weekday() in set(skip_days)
    )
    skip_label = format_skip_days(skip_days) if skip_days else "every day"
    reply = (
        f"**Plan created** — {inserted} tasks over **{active_days} active day(s)** "
        f"({start.isoformat()} → {end.isoformat()}, {skip_label}).\n"
        f"Settings: **{difficulty}**, {tasks_per_day} task(s)/active day.\n"
        f"Progress: {prog.get('done', 0)}/{prog.get('total', 0)} done.\n\n"
        "Your tasks are shown in the calendar panel below. "
        "Ask **show my plan** or **review goals** anytime."
    )
    trace.append({"phase": "goal_create", "tasks_inserted": inserted, "planner": "goal_task_gen"})
    return (
        reply,
        {"phase": "active", "answers": answers, "schedule_display": display},
        trace,
    )


def _plan_goal_context(store: GoalPlanSessionStore, plan_id: str, plan_row: dict[str, Any]) -> tuple[str, str]:
    goal_title = ""
    objective = ""
    gid = str(plan_row.get("goal_id") or "")
    for g in store.list_user_goals():
        if str(g.get("id")) == gid:
            goal_title = str(g.get("title") or "").strip()
            break
    sess = store._load_plan_session(plan_id) or {}
    answers = sess.get("answers") or {}
    if answers.get("objective", {}).get("parsed"):
        objective = str(answers["objective"]["parsed"]).strip()
    elif plan_row.get("plan_notes"):
        objective = str(plan_row.get("plan_notes") or "").strip()
    return goal_title, objective


def _handle_tasks_summary(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str = "",
    *,
    history: list[dict[str, Any]] | None = None,
    server_today: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """List this plan's goal tasks only — no calendar events or free-time reads."""
    from todai.database.utils.dates import format_today_reply, is_today_question
    from todai.goal_planner.today_context import get_server_today_for_user

    trace: list[dict[str, Any]] = [{"phase": "goal_tasks_summary"}]
    if not server_today:
        server_today = get_server_today_for_user(store.api_user_id)
    if is_today_question(message):
        trace.append({"phase": "today_reply", "source": "server"})
        return format_today_reply(server_today), {}, trace
    plan_row = store.get_plan_row(plan_id)
    if not plan_row:
        return "Plan not found.", {}, trace

    start = date.fromisoformat(str(plan_row["start_date"])[:10])
    end = date.fromisoformat(str(plan_row["end_date"])[:10])
    all_tasks = store.list_goal_tasks(plan_id)
    trace.append({"phase": "tasks_loaded", "goal_tasks": len(all_tasks)})

    today_iso = (server_today or {}).get("iso")
    query = parse_task_summary_query(
        message,
        start=start,
        end=end,
        tasks=all_tasks,
        today_iso=today_iso,
    )
    trace.append(
        {
            "phase": "task_query",
            "scope": query.scope,
            "dates": list(query.dates),
            "day_label": query.day_label or None,
            "matched": len(query.matched_tasks),
            "server_today": today_iso,
        }
    )

    if query.scope == "task_match":
        view_tasks = list(query.matched_tasks)
    elif query.scope == "guidance":
        if query.matched_tasks:
            view_tasks = list(query.matched_tasks)
        elif query.dates:
            view_tasks = filter_tasks_by_dates(all_tasks, query.dates)
        else:
            view_tasks = all_tasks
    elif query.scope == "day":
        view_tasks = filter_tasks_by_dates(all_tasks, query.dates)
    elif query.scope == "progress_only":
        view_tasks = []
    else:
        view_tasks = all_tasks

    goal_title, objective = _plan_goal_context(store, plan_id, plan_row)
    display = build_goal_plan_schedule_display(
        all_tasks,
        start=start,
        end=end,
        goal_objective=objective,
        tool_results=None,
    )
    reply, source = compose_task_summary_reply(
        message=message,
        history=history,
        query=query,
        view_tasks=view_tasks,
        all_tasks=all_tasks,
        start=start,
        end=end,
        schedule_display=display,
        goal_title=goal_title,
        objective=objective,
        server_today=server_today,
    )
    trace.append({"phase": "task_summary_reply", "source": source})
    return reply, {"schedule_display": display}, trace


def _handle_schedule_read(
    store: GoalPlanSessionStore,
    plan_id: str,
    session: dict[str, Any],
    message: str,
    *,
    router_tools: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = [{"phase": "goal_schedule_read", "message": message[:80]}]
    plan_row = store.get_plan_row(plan_id)
    if not plan_row:
        return "Plan not found.", {}, trace

    start = date.fromisoformat(str(plan_row["start_date"])[:10])
    end = date.fromisoformat(str(plan_row["end_date"])[:10])

    tasks = store.list_goal_tasks(plan_id)

    planned = normalize_router_tools(router_tools)
    read_calls: list[dict[str, Any]] = []
    want_range = not planned or any(t.get("tool") == "get_schedule_range" for t in planned)
    want_free = not planned or any(t.get("tool") == "get_free_time" for t in planned)
    if want_range:
        read_calls.append(
            {
                "tool": "get_schedule_range",
                "arguments": {"from": start.isoformat(), "to": end.isoformat()},
            }
        )
    if want_free:
        read_calls.append(
            {
                "tool": "get_free_time",
                "arguments": {"from": start.isoformat(), "to": end.isoformat()},
            }
        )

    with user_store(store.api_user_id) as us:
        results, errs = execute_read_tools(us, read_calls)
    trace.append(
        {
            "phase": "prefetch",
            "calls": [c["tool"] for c in read_calls],
            "errors": errs,
            "goal_tasks": len(tasks),
        }
    )
    _, objective = _plan_goal_context(store, plan_id, plan_row)
    display = build_goal_plan_schedule_display(
        tasks,
        start=start,
        end=end,
        goal_objective=objective,
        tool_results=results,
    )
    reply = format_plan_schedule_reply(
        tasks=tasks,
        tool_results=results,
        start=start,
        end=end,
        schedule_display=display,
    )
    return reply, {"schedule_display": display}, trace

