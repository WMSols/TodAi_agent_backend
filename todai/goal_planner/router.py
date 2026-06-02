"""Rule-based routing for goal planner turns (no LLM router)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from todai.goal_planner.interrogation import answers_complete

GoalRoute = Literal[
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_schedule_read",
    "goal_goals_list",
    "goal_delete",
    "goal_edit",
    "goal_chat",
]

_SCHEDULE_PATTERNS = re.compile(
    r"\b(free time|free slot|calendar|schedule|what.?s on|busy|available|"
    r"my plan|show tasks|show my|my schedule|view my|give me|can i view|"
    r"this week|tomorrow|my tasks|daily tasks)\b",
    re.I,
)
_GOALS_LIST_PATTERNS = re.compile(
    r"\b(review goals?|my goals?|list goals?|show goals?|view goals?|all goals?)\b",
    re.I,
)
_DELETE_PATTERNS = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(goal|plan|tasks?)\b|"
    r"\b(goal|plan|tasks?)\b.*\b(delete|remove|cancel|clear)\b",
    re.I,
)
_DELETE_ALL_PATTERNS = re.compile(
    r"\b(delete|remove|clear)\b.*\b(all|every)\b.*\b(goal|plan)",
    re.I,
)
_EDIT_PATTERNS = re.compile(
    r"\b(move|reschedule|edit task|skip|swap|easier|harder|mark done|complete task)\b",
    re.I,
)
_CREATE_PATTERNS = re.compile(
    r"\b(create|generate|build|make)\s+(the\s+)?(plan|tasks|schedule)\b",
    re.I,
)


@dataclass(frozen=True)
class GoalRouterOutput:
    route: GoalRoute
    reason: str


def route_goal_turn(
    *,
    message: str,
    phase: str,
    answers: dict,
) -> GoalRouterOutput:
    text = (message or "").strip()
    complete = answers_complete(answers)

    if phase == "active":
        if _DELETE_ALL_PATTERNS.search(text) or _DELETE_PATTERNS.search(text):
            return GoalRouterOutput("goal_delete", "active_delete_keywords")
        if _GOALS_LIST_PATTERNS.search(text):
            return GoalRouterOutput("goal_goals_list", "active_goals_list")
        if _EDIT_PATTERNS.search(text):
            return GoalRouterOutput("goal_edit", "active_edit_keywords")
        if _SCHEDULE_PATTERNS.search(text):
            return GoalRouterOutput("goal_schedule_read", "active_schedule_keywords")
        return GoalRouterOutput("goal_chat", "active_general")

    if phase == "confirm":
        return GoalRouterOutput("goal_confirm", "awaiting_confirmation")

    if complete and (_CREATE_PATTERNS.search(text) or phase == "ready"):
        return GoalRouterOutput("goal_create", "answers_complete_create")

    if phase in ("interrogate", "intake", "clarify", ""):
        if complete and re.search(r"\b(yes|create|generate)\b", text, re.I):
            return GoalRouterOutput("goal_create", "interrogate_done_yes")
        if _SCHEDULE_PATTERNS.search(text) and not complete:
            return GoalRouterOutput("goal_schedule_read", "mid_intake_schedule")
        if _DELETE_PATTERNS.search(text):
            return GoalRouterOutput("goal_delete", "mid_intake_delete")
        if _EDIT_PATTERNS.search(text):
            return GoalRouterOutput("goal_edit", "mid_intake_edit")
        return GoalRouterOutput("goal_interrogate", "collecting_answers")

    if phase == "creating":
        return GoalRouterOutput("goal_chat", "already_creating")

    return GoalRouterOutput("goal_interrogate", "default")
