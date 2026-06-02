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
from todai.goal_planner.tools import execute_delete_all_goals, execute_delete_plan, execute_list_goals
from todai.goal_planner.router import route_goal_turn
from todai.goal_planner.interrogation import (
    QUESTIONS,
    STEPS,
    _answer_label,
    answers_complete,
    confirmation_prompt,
    current_step,
    is_active_acknowledgment,
    next_missing_step,
    parse_answer,
    parse_confirmation,
)
from todai.goal_planner.session_store import GoalPlanSessionStore

from todai.agent.tools.calendar import execute_read_tools


def orchestrate_goal_turn(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
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
    route_out = route_goal_turn(message=message, phase=phase, answers=answers)
    route = route_out.route
    trace: list[dict[str, Any]] = [{"phase": "goal_router", "route": route, "reason": route_out.reason}]

    if route == "goal_interrogate":
        reply, patch = _handle_interrogate(store, plan_id, session, message)
        return reply, patch, route, trace

    if route == "goal_confirm":
        reply, patch = _handle_confirm(session, message)
        if patch.get("phase") == "ready":
            reply, patch, create_trace = _handle_create(store, plan_id, {**session, **patch})
            trace.extend(create_trace)
            return reply, patch, "goal_create", trace
        return reply, patch, route, trace

    if route == "goal_create":
        reply, patch, create_trace = _handle_create(store, plan_id, session)
        trace.extend(create_trace)
        return reply, patch, route, trace

    if route == "goal_schedule_read":
        reply, patch, read_trace = _handle_schedule_read(store, plan_id, session, message)
        trace.extend(read_trace)
        return reply, patch, route, trace

    if route == "goal_goals_list":
        reply, patch, list_trace = _handle_goals_list(store, plan_id)
        trace.extend(list_trace)
        return reply, patch, route, trace

    if route == "goal_delete":
        reply, patch, del_trace = _handle_delete(store, plan_id, message)
        trace.extend(del_trace)
        return reply, patch, route, trace

    if route == "goal_edit":
        reply = (
            "Task editing (move, mark done) isn't available yet. Try:\n"
            "• **show my plan** — view tasks\n"
            "• **delete my goals** — remove this plan's tasks\n"
            "• **review goals** — list all goals"
        )
        return reply, {}, route, trace

    if phase == "active":
        if is_active_acknowledgment(message):
            reply = (
                "Your 7-day plan is active. Ask **“show my plan”** or **“my schedule”** "
                "to see goal tasks and calendar events."
            )
        else:
            reply = (
                "Your plan is already created. Try:\n"
                "• **show my plan** — goal tasks for the week\n"
                "• **my schedule** — calendar + tasks\n"
                "• **new plan** — use the “New plan” button in the panel above"
            )
        return reply, {}, "goal_chat", trace

    reply = (
        "I'll ask **4 short questions**, then build a 7-day task plan in your free time slots. "
        "Answer each question in order."
    )
    return reply, {}, "goal_chat", trace


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
                f"{result.hint}\n\n{QUESTIONS[step]}",
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
            return f"{ack}\n\n{QUESTIONS[nxt]}", {"phase": "interrogate", "intake_step": nxt, "answers": answers}
        session["phase"] = "confirm"
        store._save_plan_session(plan_id, session)
        return f"{ack}\n\n{confirmation_prompt(answers)}", {"phase": "confirm", "answers": answers}

    return QUESTIONS[step], {"phase": "interrogate", "intake_step": step}


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
    answers = session.get("answers") or {}
    choice = parse_confirmation(message)
    if choice == "yes":
        return "", {"phase": "ready"}
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
                f"Okay — let's update **{step.replace('_', ' ')}**.\n\n{QUESTIONS[step]}",
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
            f"I still need a valid answer for **{step.replace('_', ' ')}**.\n\n{QUESTIONS[step]}",
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
    task_rows = enrich_tasks_with_descriptions(
        objective=objective,
        difficulty=difficulty,
        tasks=task_rows,
        minutes_per_day=minutes_per_day,
        tasks_per_day=tasks_per_day,
    )
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
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    plan_row = store.get_plan_row(plan_id)
    if not plan_row:
        return "Plan not found.", {}, trace

    start = date.fromisoformat(str(plan_row["start_date"])[:10])
    end = date.fromisoformat(str(plan_row["end_date"])[:10])

    tasks = store.list_goal_tasks(plan_id)

    with user_store(store.api_user_id) as us:
        results, errs = execute_read_tools(
            us,
            [
                {
                    "tool": "get_schedule_range",
                    "arguments": {"from": start.isoformat(), "to": end.isoformat()},
                },
                {
                    "tool": "get_free_time",
                    "arguments": {"from": start.isoformat(), "to": end.isoformat()},
                },
            ],
        )
    trace.append(
        {
            "phase": "prefetch",
            "calls": ["get_schedule_range", "get_free_time"],
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


def _handle_goals_list(
    store: GoalPlanSessionStore,
    plan_id: str,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = [{"phase": "goal_tool", "tool": "list_goals"}]
    data = execute_list_goals(store)
    goals = data.get("goals") or []
    plans = data.get("plans") or []
    lines = ["**Your goals**", ""]
    if not goals:
        lines.append("No goals stored yet. Start a new plan above.")
    else:
        for g in goals[:10]:
            lines.append(
                f"• **{g.get('title', 'Goal')}** — {g.get('status', '?')} "
                f"({g.get('difficulty', 'medium')})"
            )
    lines.append("")
    lines.append("**Week plans**")
    if not plans:
        lines.append("No week plans yet.")
    else:
        for p in plans[:10]:
            mark = " ← current" if str(p.get("id")) == plan_id else ""
            lines.append(
                f"• {p.get('start_date')} → {p.get('end_date')} "
                f"[{p.get('status', '?')}]{mark}"
            )
    patch: dict[str, Any] = {}
    plan_row = store.get_plan_row(plan_id)
    if plan_row:
        start = date.fromisoformat(str(plan_row["start_date"])[:10])
        end = date.fromisoformat(str(plan_row["end_date"])[:10])
        tasks = store.list_goal_tasks(plan_id)
        display = build_goal_plan_schedule_display(tasks, start=start, end=end)
        patch["schedule_display"] = display
        prog = display.get("progress") or {}
        lines.append("")
        lines.append(
            f"**Current plan progress:** {prog.get('done', 0)}/{prog.get('total', 0)} done "
            f"({prog.get('percent', 0)}%)"
        )
    return "\n".join(lines), patch, trace


def _handle_delete(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    from todai.goal_planner.router import _DELETE_ALL_PATTERNS  # noqa: PLC2701

    trace: list[dict[str, Any]] = []
    if _DELETE_ALL_PATTERNS.search(message):
        trace.append({"phase": "goal_tool", "tool": "delete_all_goals"})
        result = execute_delete_all_goals(store)
        return (
            f"Removed **{result.get('goals_deleted', 0)}** goal(s), "
            f"**{result.get('plans_deleted', 0)}** plan(s), "
            f"**{result.get('tasks_deleted', 0)}** task(s) from your account.",
            {"phase": "interrogate", "answers": {}},
            trace,
        )
    trace.append({"phase": "goal_tool", "tool": "delete_plan"})
    result = execute_delete_plan(store, plan_id)
    return (
        f"Deleted **{result.get('tasks_deleted', 0)}** task(s) for this plan. "
        "The plan is back to **draft** — you can start fresh or answer the questions again.",
        {"phase": "interrogate", "answers": {}},
        trace,
    )
