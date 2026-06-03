"""Phase guards applied after LLM router (same role as calendar routing_guards)."""

from __future__ import annotations

from todai.goal_planner.interrogation import answers_complete
from todai.goal_planner.routing.contracts import GoalRouterModel


def apply_goal_route_guards(
    out: GoalRouterModel,
    *,
    phase: str,
    answers: dict,
    ui_mode: str = "my_goals",
) -> tuple[GoalRouterModel, str]:
    """Return possibly adjusted router output and guard reason suffix."""
    complete = answers_complete(answers)
    reasons: list[str] = []

    if phase == "confirm":
        if out.route != "goal_confirm":
            out = out.model_copy(update={"route": "goal_confirm", "manage_action": "none"})
            reasons.append("force_confirm_phase")
        return out, "|".join(reasons) if reasons else "ok"

    intake_only = ui_mode == "new_goal"
    if intake_only and phase in ("interrogate", "intake", "clarify", "") and not complete:
        if out.route in ("goal_create",) and not complete:
            out = out.model_copy(update={"route": "goal_interrogate", "manage_action": "none"})
            reasons.append("block_create_until_answers")
        elif out.route == "goal_chat":
            out = out.model_copy(update={"route": "goal_interrogate", "manage_action": "none"})
            reasons.append("interrogate_over_chat")
        return out, "|".join(reasons) if reasons else "ok"

    if phase == "active" and out.route == "goal_interrogate":
        out = out.model_copy(update={"route": "goal_chat", "manage_action": "none"})
        reasons.append("active_not_interrogate")

    if phase == "creating":
        out = out.model_copy(update={"route": "goal_chat", "manage_action": "none"})
        reasons.append("creating_wait")

    return out, "|".join(reasons) if reasons else "ok"
