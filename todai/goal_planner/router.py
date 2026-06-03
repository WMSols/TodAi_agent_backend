"""Goal planner routing facade — Groq router + rules fallback + phase guards."""



from __future__ import annotations



from dataclasses import dataclass

from typing import Any, Literal



from todai.goal_planner.routing.contracts import GoalRouterModel

from todai.goal_planner.routing.guards import apply_goal_route_guards

from todai.goal_planner.routing.llm_router import route_goal_turn_llm

from todai.goal_planner.routing.rules_router import route_goal_turn_rules

from todai.goal_planner.routing.routing_guards import apply_goal_router_guards



GoalRoute = Literal[

    "goal_interrogate",

    "goal_confirm",

    "goal_create",

    "goal_manage",

    "goal_schedule_read",

    "goal_chat",

    "goal_goals_list",

    "goal_delete",

    "goal_edit",

]





@dataclass(frozen=True)

class GoalRouterOutput:

    route: str

    manage_action: str = "none"

    tools: tuple[dict[str, Any], ...] = ()

    reason: str = ""

    source: str = "rules"

    guard_notes: tuple[dict[str, Any], ...] = ()





def route_goal_turn(

    *,

    message: str,

    phase: str,

    answers: dict,

    plan_id: str = "",

    session: dict[str, Any] | None = None,

    history: list[dict[str, Any]] | None = None,

    ui_mode: str = "my_goals",
    needs_task_setup: bool = False,
) -> GoalRouterOutput:

    """

    Classify one goal-plan turn (same layering as calendar: LLM → guards → handlers).



    Falls back to regex rules if Groq is off or returns invalid JSON.

    """

    session = session or {}

    from todai.goal_planner.routing.context import groq_goal_router_context



    pending = session.get("pending_manage")

    if pending:

        kind = str(pending.get("kind") or "")

        action = {

            "delete_all": "delete_all",

            "delete_plan": "delete_plan",

            "delete_goal": "delete_goal",

        }.get(kind, "none")

        tool_name = {
            "delete_all": "delete_all_goals",
            "delete_plan": "delete_plan",
            "delete_goal": "delete_goal",
        }.get(kind)
        tools = ({"tool": tool_name, "arguments": {}},) if tool_name else ()

        return GoalRouterOutput(

            route="goal_manage",

            manage_action=action,

            tools=tools,

            reason="pending_manage",

            source="session",

        )



    routing_context = groq_goal_router_context(history or [], message, session=session)



    model, errs, dbg = route_goal_turn_llm(

        current_message=message,

        routing_context=routing_context or None,

        phase=phase,

        answers=answers,

        plan_id=plan_id,

        session=session,

        ui_mode=ui_mode,
        needs_task_setup=needs_task_setup,
    )

    source = "groq"

    if model is None:
        from todai.goal_planner.routing.rules_router import match_setup_intent

        if needs_task_setup:
            model = match_setup_intent(message, answers)
        if model is None:
            model = route_goal_turn_rules(message=message, phase=phase, answers=answers)
        source = "rules_fallback"

        if errs:

            reason = f"invalid_router|{'|'.join(e.get('code', '') for e in errs)}"

        else:

            reason = "rules_fallback"

    else:

        reason = "groq"



    model, guard_notes = apply_goal_router_guards(
        model,
        message=message,
        ui_mode=ui_mode,
        session=session,
        needs_task_setup=needs_task_setup,
    )



    model, phase_guard_reason = apply_goal_route_guards(

        model, phase=phase, answers=answers, ui_mode=ui_mode

    )

    if phase_guard_reason != "ok":

        reason = f"{reason}|{phase_guard_reason}"



    if dbg and dbg.get("mock"):

        source = "rules_mock"



    raw_route = model.route

    manage_action = model.manage_action

    if raw_route in ("goal_goals_list", "goal_delete", "goal_edit"):

        if manage_action == "none":

            manage_action = {

                "goal_goals_list": "list",

                "goal_delete": "delete_goal",

                "goal_edit": "edit",

            }[raw_route]

        route = "goal_manage"

    else:

        route = raw_route



    return GoalRouterOutput(

        route=route,

        manage_action=manage_action,

        tools=tuple(model.tools),

        reason=reason,

        source=source,

        guard_notes=tuple(guard_notes),

    )

