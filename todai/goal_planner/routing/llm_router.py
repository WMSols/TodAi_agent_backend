"""Groq goal planner router (mirrors todai.agent.routing.router.run_router)."""

from __future__ import annotations

import json
from typing import Any

from todai.goal_planner.routing.context import groq_goal_router_context
from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.routing.contracts import GoalRouterModel, parse_goal_router_output
from todai.goal_planner.routing.prompts import GOAL_ROUTER_SYSTEM
from todai.goal_planner.routing.rules_router import route_goal_turn_rules


def _build_goal_router_user_context(
    *,
    current_message: str,
    phase: str,
    answers: dict,
    plan_id: str,
    session: dict[str, Any],
    ui_mode: str = "my_goals",
    needs_task_setup: bool = False,
) -> str:
    answers_complete_flag = all(
        isinstance(answers.get(k), dict) and answers[k].get("valid")
        for k in ("objective", "difficulty", "tasks_per_day", "minutes_per_day")
    )
    pending = session.get("pending_manage") or {}
    payload = {
        "CURRENT_USER_MESSAGE": current_message,
        "GOAL_CONTEXT": {
            "phase": phase,
            "plan_id": plan_id,
            "ui_mode": ui_mode,
            "answers_complete": answers_complete_flag,
            "intake_step": session.get("intake_step"),
            "title": session.get("title"),
            "pending_manage": pending.get("kind") if pending else None,
            "plan_status": session.get("plan_status"),
            "needs_task_setup": needs_task_setup,
            "tasks_created": bool(session.get("tasks_created")),
        },
    }
    if routing_context_note := session.get("_router_hint"):
        payload["GOAL_CONTEXT"]["hint"] = routing_context_note
    return json.dumps(payload, ensure_ascii=False)


def mock_route_goal(message: str, *, phase: str, answers: dict) -> dict[str, Any]:
    model = route_goal_turn_rules(message=message, phase=phase, answers=answers)
    return {
        "route": model.route,
        "manage_action": model.manage_action,
        "tools": model.tools,
        "_groq_debug": {"ok": True, "mock": True, "source": "rules"},
    }


def route_goal_turn_llm(
    *,
    current_message: str,
    routing_context: list[dict[str, str]] | None,
    phase: str,
    answers: dict,
    plan_id: str,
    session: dict[str, Any],
    ui_mode: str = "my_goals",
    needs_task_setup: bool = False,
) -> tuple[GoalRouterModel | None, list[dict[str, Any]], dict[str, Any] | None]:
    if not GROQ_API_KEY:
        raw = mock_route_goal(current_message, phase=phase, answers=answers)
        out, errs = parse_goal_router_output(raw)
        return out, errs, raw.get("_groq_debug")

    ctx = _build_goal_router_user_context(
        current_message=current_message,
        phase=phase,
        answers=answers,
        plan_id=plan_id,
        session=session,
        ui_mode=ui_mode,
        needs_task_setup=needs_task_setup,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": GOAL_ROUTER_SYSTEM}]
    if routing_context:
        messages.append(
            {
                "role": "user",
                "content": "ROUTING_CONTEXT (prior turns for follow-ups):\n"
                + json.dumps(routing_context, ensure_ascii=False),
            }
        )
    messages.append({"role": "user", "content": ctx})

    raw = groq_chat_json(messages, phase="goal_router", max_tokens=140, temperature=0)
    router_dbg = raw.pop("_groq_debug", None) if isinstance(raw, dict) else None
    if isinstance(router_dbg, dict):
        router_dbg["source"] = "groq"
        router_dbg["prompt_bundle"] = "goal_router_v2"
        router_dbg["prompt_chars"] = {
            "system": len(GOAL_ROUTER_SYSTEM),
            "routing_context": sum(len(m.get("content") or "") for m in (routing_context or [])),
            "user_ctx": len(ctx),
        }

    out, errs = parse_goal_router_output(raw if isinstance(raw, dict) else {})
    return out, errs, router_dbg
