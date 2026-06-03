"""Goal planner turn orchestration: router → handlers."""

from __future__ import annotations

import re
from datetime import date
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
    confirmation_prompt,
    current_step,
    next_missing_step,
    parse_answer,
    parse_confirmation,
    question_for_step,
)
from todai.goal_planner.ai_intake import handle_ai_confirm, handle_ai_intake_turn, uses_ai_intake
from todai.goal_planner.plan_state import plan_needs_task_setup
from todai.goal_planner.session_store import GoalPlanSessionStore

from todai.agent.tools.calendar import execute_read_tools
from todai.goal_planner.routing.contracts import normalize_router_tools

UiMode = str  # "my_goals" | "new_goal"


def orchestrate_goal_turn(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    ui_mode: str = "my_goals",
) -> tuple[str, dict[str, Any], str, list[dict[str, Any]]]:
    """
    Returns (reply, session_patch, route, tool_trace).
    Caller merges session_patch and persists messages.
    """
    session = store._load_plan_session(plan_id)
    if not session:
        session = {"phase": "interrogate", "answers": {}}

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
    )
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

    if not intake_mode and needs_setup and route in ("goal_interrogate", "goal_confirm", "goal_create"):
        reply = (
            "This plan doesn't have tasks yet. Open the **New goal** tab to finish AI setup "
            "(title + description → tailored questions → build 7-day tasks)."
        )
        trace.append({"phase": "route_hint", "reason": "setup_on_new_goal_tab"})
        return reply, {}, "goal_chat", trace

    if route == "goal_interrogate" and setup_mode:
        if uses_ai_intake(session, ui_mode):
            reply, patch = handle_ai_intake_turn(session, message)
            session.update(patch)
            trace.append({"phase": "goal_ai_intake", "intake_style": "ai"})
            return reply, patch, route, trace
        reply, patch = _handle_interrogate(store, plan_id, session, message)
        return reply, patch, route, trace

    if route == "goal_confirm" and setup_mode:
        if uses_ai_intake(session, ui_mode):
            reply, patch = handle_ai_confirm(session, message)
            session.update(patch)
            trace.append({"phase": "goal_ai_intake", "step": "confirm"})
            if patch.get("phase") == "ready":
                reply, patch, create_trace = _handle_create(store, plan_id, {**session, **patch})
                trace.extend(create_trace)
                return reply, patch, "goal_create", trace
            return reply, patch, route, trace
        reply, patch = _handle_confirm(session, message)
        if patch.get("phase") == "ready":
            reply, patch, create_trace = _handle_create(store, plan_id, {**session, **patch})
            trace.extend(create_trace)
            return reply, patch, "goal_create", trace
        return reply, patch, route, trace

    if route == "goal_create" and setup_mode:
        reply, patch, create_trace = _handle_create(store, plan_id, session)
        trace.extend(create_trace)
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
        "I'll ask **4 short questions**, then build a 7-day task plan in your free time slots. "
        "Answer each question in order."
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


def _message_answers_intake_step(session: dict[str, Any], message: str) -> bool:
    """True when message is a valid answer for the current intake step (not small talk)."""
    answers = session.get("answers") or {}
    step = current_step(session) or next_missing_step(answers)
    if not step:
        return False
    result = parse_answer(step, message, default_objective=_default_objective(session))
    return bool(result.valid)


def _default_objective(session: dict[str, Any]) -> str:
    title = (session.get("title") or "").strip()
    desc = (session.get("description") or "").strip()
    if title and desc:
        return f"{title} — {desc}"
    return title or desc


def _handle_interrogate(
    store: GoalPlanSessionStore,
    plan_id: str,
    session: dict[str, Any],
    message: str,
) -> tuple[str, dict[str, Any]]:
    answers = session.setdefault("answers", {})
    step = current_step(session) or next_missing_step(answers) or STEPS[0]

    if answers_complete(answers):
        session["phase"] = "confirm"
        store._save_plan_session(plan_id, session)
        return confirmation_prompt(answers), {"phase": "confirm"}

    if step and (not answers.get(step) or not answers[step].get("valid")):
        result = parse_answer(step, message, default_objective=_default_objective(session))
        if not result.valid:
            return (
                f"{result.hint}\n\n{question_for_step(step, session)}",
                {"phase": "interrogate", "intake_step": step},
            )
        answers[step] = {
            "valid": True,
            "parsed": result.parsed,
            "raw": message.strip(),
            "display": result.display or "",
        }
        ack = _ack(step, result)
        nxt = next_missing_step(answers)
        if nxt:
            session["phase"] = "interrogate"
            session["intake_step"] = nxt
            store._save_plan_session(plan_id, session)
            return (
                f"{ack}\n\n{question_for_step(nxt, session)}",
                {"phase": "interrogate", "intake_step": nxt, "answers": answers},
            )
        session["phase"] = "confirm"
        store._save_plan_session(plan_id, session)
        return f"{ack}\n\n{confirmation_prompt(answers)}", {"phase": "confirm", "answers": answers}

    return question_for_step(step, session), {"phase": "interrogate", "intake_step": step}


def _ack(step: str, result: Any) -> str:
    label = getattr(result, "display", None) or str(getattr(result, "parsed", ""))
    if step == "objective":
        return f"Saved — objective: {label}"
    if step == "difficulty":
        return f"Saved — difficulty: **{label}**"
    if step == "tasks_per_day":
        return f"Saved — **{label}** task(s) per day"
    if step == "minutes_per_day":
        return f"Saved — daily time: **{label}**"
    return "Saved."


def _handle_confirm(session: dict[str, Any], message: str) -> tuple[str, dict[str, Any]]:
    from todai.goal_planner.interrogation import try_apply_confirm_edits

    answers = dict(session.get("answers") or {})
    default_obj = _default_objective(session)
    answers, ack = try_apply_confirm_edits(message, answers, default_objective=default_obj)
    choice = parse_confirmation(message)
    if ack:
        if choice == "yes":
            return "", {"phase": "ready", "answers": answers}
        return (
            f"Updated — {ack}\n\n{confirmation_prompt(answers)}\n\n(Reply **yes** to build the plan.)",
            {"phase": "confirm", "answers": answers},
        )
    if choice == "yes":
        return "", {"phase": "ready", "answers": answers}
    if choice == "no":
        return (
            "No problem. Tell me which answer to change (objective, difficulty, tasks per day, or minutes per day).",
            {"phase": "interrogate"},
        )
    if re_mentions_step(message):
        step = _step_from_change_request(message)
        if step:
            if step in answers:
                answers[step] = {"valid": False}
            return (
                f"Okay — let's update **{step.replace('_', ' ')}**.\n\n{question_for_step(step, session)}",
                {"phase": "interrogate", "intake_step": step, "answers": answers},
            )
    return (
        f"{confirmation_prompt(answers)}\n\n(Reply **yes** to build the plan.)",
        {"phase": "confirm"},
    )


def re_mentions_step(message: str) -> bool:
    t = message.lower()
    return any(k in t for k in ("objective", "difficulty", "task", "minute", "time", "easy", "hard"))


def _step_from_change_request(message: str) -> str | None:
    t = message.lower()
    if "objective" in t or "goal" in t:
        return "objective"
    if "difficult" in t or "easy" in t or "hard" in t or "medium" in t:
        return "difficulty"
    if "task" in t and "day" in t:
        return "tasks_per_day"
    if "minute" in t or "hour" in t or "time" in t:
        return "minutes_per_day"
    return None


def _handle_create(
    store: GoalPlanSessionStore,
    plan_id: str,
    session: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    answers = session.get("answers") or {}
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
    difficulty = str(answers["difficulty"]["parsed"])
    tasks_per_day = int(answers["tasks_per_day"]["parsed"])
    minutes_per_day = int(answers["minutes_per_day"]["parsed"])

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
        tool_results=tool_results,
    )
    prog = display.get("progress") or {}
    time_label = _answer_label(answers, "minutes_per_day")

    reply = (
        f"**Plan created** — {inserted} tasks over {days_count} days "
        f"({start.isoformat()} → {end.isoformat()}).\n"
        f"Settings: **{difficulty}**, {tasks_per_day} task(s)/day, {time_label}.\n"
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
    display = build_goal_plan_schedule_display(
        tasks,
        start=start,
        end=end,
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

