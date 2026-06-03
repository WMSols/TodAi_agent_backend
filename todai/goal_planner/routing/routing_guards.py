"""Post-LLM goal router guards — normalize route, manage_action, and tool plan (calendar-style)."""

from __future__ import annotations

from typing import Any

from todai.goal_planner.routing.contracts import GoalRouterModel, normalize_router_tools
from todai.goal_planner.routing.rules_router import match_setup_intent

_TOOL_TO_MANAGE: dict[str, str] = {
    "list_goals_with_progress": "list",
    "list_goals": "list",
    "delete_plan": "delete_plan",
    "delete_goal": "delete_goal",
    "delete_all_goals": "delete_all",
}


def default_tools_for_goal_route(
    route: str,
    *,
    manage_action: str = "none",
) -> list[dict[str, Any]]:
    if route == "goal_schedule_read":
        return [
            {"tool": "get_schedule_range", "arguments": {}},
            {"tool": "get_free_time", "arguments": {}},
        ]
    if route != "goal_manage":
        return []
    if manage_action == "list":
        return [{"tool": "list_goals_with_progress", "arguments": {}}]
    if manage_action == "delete_plan":
        return [{"tool": "delete_plan", "arguments": {}}]
    if manage_action == "delete_goal":
        return [{"tool": "delete_goal", "arguments": {}}]
    if manage_action == "delete_all":
        return [{"tool": "delete_all_goals", "arguments": {}}]
    return []


def apply_goal_router_guards(
    out: GoalRouterModel,
    *,
    message: str,
    ui_mode: str = "my_goals",
    session: dict[str, Any] | None = None,
    needs_task_setup: bool = False,
) -> tuple[GoalRouterModel, list[dict[str, Any]]]:
    """
    Merge router tools with route defaults; infer manage_action from tools when missing.
    Returns (model, trace_notes).
    """
    notes: list[dict[str, Any]] = []
    manage_action = out.manage_action
    tools = normalize_router_tools(out.tools)
    answers = (session or {}).get("answers") or {}

    if needs_task_setup and ui_mode == "new_goal":
        setup = match_setup_intent(message, answers)
        if setup:
            out = setup
            manage_action = out.manage_action
            tools = normalize_router_tools(out.tools)
            notes.append({"phase": "router_guard", "reason": "setup_intent", "route": out.route})
        elif out.route == "goal_create":
            notes.append({"phase": "router_guard", "reason": "allow_create_setup"})

    if out.route == "goal_manage" and manage_action == "none" and tools:
        for t in tools:
            inferred = _TOOL_TO_MANAGE.get(str(t.get("tool") or ""))
            if inferred:
                manage_action = inferred
                notes.append({"phase": "router_guard", "reason": "infer_manage_from_tools", "action": inferred})
                break

    if out.route in ("goal_schedule_read", "goal_manage") and not tools:
        tools = default_tools_for_goal_route(out.route, manage_action=manage_action)
        if tools:
            notes.append({"phase": "router_guard", "reason": "default_tools", "tools": [t["tool"] for t in tools]})

    if ui_mode == "my_goals" and out.route == "goal_interrogate" and not needs_task_setup:
        out = out.model_copy(update={"route": "goal_chat", "manage_action": "none", "tools": []})
        notes.append({"phase": "router_guard", "reason": "my_goals_no_intake"})
        return out, notes

    if manage_action != out.manage_action or tools != out.tools:
        out = out.model_copy(update={"manage_action": manage_action, "tools": tools})

    return out, notes
