"""Regex fallback router when Groq is unavailable or returns invalid JSON."""

from __future__ import annotations

import re

from todai.goal_planner.interrogation import answers_complete
from todai.goal_planner.routing.contracts import GoalRouterModel

_SCHEDULE_PATTERNS = re.compile(
    r"\b(free time|free slot|calendar|schedule|what.?s on|busy|available|"
    r"my plan|show tasks|show my|my schedule|view my|give me|can i view|"
    r"this week|tomorrow|my tasks|daily tasks)\b",
    re.I,
)
_GOALS_LIST_PATTERNS = re.compile(
    r"\b(review goals?|my goals?|list goals?|show goals?|view goals?|all goals?|progress)\b",
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
_DELETE_SHORT_PATTERNS = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(it|this|that)\b",
    re.I,
)
_DELETE_GOAL_PATTERNS = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(goal|goals)\b|"
    r"\b(goal|goals)\b.*\b(delete|remove|cancel|clear)\b",
    re.I,
)
_DELETE_PLAN_ONLY_PATTERNS = re.compile(
    r"\b(delete|remove|clear)\b.*\b(tasks? only|only tasks?)\b|"
    r"\b(clear|remove)\b.*\btasks?\b(?!.*\bgoal)|"
    r"\breset\b.*\b(plan|tasks?|draft)\b|"
    r"\b(delete|remove)\b.*\bdraft\b|"
    r"\bkeep\b.*\bgoal\b.*\b(delete|remove)\b",
    re.I,
)
_DELETE_FULL_PLAN_PHRASE = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(plan|program|programme)\b|"
    r"\b(delete|remove)\b.*\b(weight\s*loss|fitness|workout)\b",
    re.I,
)
_WHAT_GOALS_PATTERNS = re.compile(r"\bwhat\b.*\bgoals?\b", re.I)
_SETUP_TASKS_PATTERNS = re.compile(
    r"\b(create|generate|build|make|add|set\s*up)\b.*\b(tasks?|plan|schedule)\b|"
    r"\b(tasks?|plan|schedule)\b.*\b(for|to)\b.*\b(this|my|the)?\s*goal\b|"
    r"\bcreate\b.*\b(for|to)\s*achieve\b|"
    r"\byes\b.*\b(create|build|generate)\b.*\btasks?\b",
    re.I,
)


def match_setup_intent(message: str, answers: dict | None = None) -> GoalRouterModel | None:
    """Route task generation / intake when the week plan has no tasks yet."""
    text = (message or "").strip()
    if not text or not _SETUP_TASKS_PATTERNS.search(text):
        return None
    if answers_complete(answers or {}):
        return GoalRouterModel(route="goal_create", manage_action="none", tools=[])
    return GoalRouterModel(route="goal_interrogate", manage_action="none", tools=[])


def match_operational_intent(message: str) -> GoalRouterModel | None:
    """Detect schedule / manage / list intents (any plan phase). Used before chat override."""
    text = (message or "").strip()
    if not text:
        return None
    if _DELETE_ALL_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_all",
            tools=[{"tool": "delete_all_goals"}],
        )
    if _DELETE_PLAN_ONLY_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_plan",
            tools=[{"tool": "delete_plan"}],
        )
    if (
        _DELETE_GOAL_PATTERNS.search(text)
        or _DELETE_SHORT_PATTERNS.search(text)
        or _DELETE_FULL_PLAN_PHRASE.search(text)
    ):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_goal",
            tools=[{"tool": "delete_goal"}],
        )
    if _DELETE_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_goal",
            tools=[{"tool": "delete_goal"}],
        )
    if _WHAT_GOALS_PATTERNS.search(text) or _GOALS_LIST_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="list",
            tools=[{"tool": "list_goals_with_progress"}],
        )
    if _EDIT_PATTERNS.search(text):
        return GoalRouterModel(route="goal_manage", manage_action="edit", tools=[])
    if _SCHEDULE_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_schedule_read",
            manage_action="none",
            tools=[{"tool": "get_schedule_range"}, {"tool": "get_free_time"}],
        )
    return None


def route_goal_turn_rules(
    *,
    message: str,
    phase: str,
    answers: dict,
) -> GoalRouterModel:
    text = (message or "").strip()
    complete = answers_complete(answers)

    if phase == "active":
        op = match_operational_intent(text)
        if op:
            return op
        if _DELETE_ALL_PATTERNS.search(text):
            return GoalRouterModel(route="goal_manage", manage_action="delete_all", tools=[{"tool": "delete_all_goals"}])
        if _GOALS_LIST_PATTERNS.search(text):
            return GoalRouterModel(
                route="goal_manage",
                manage_action="list",
                tools=[{"tool": "list_goals_with_progress"}],
            )
        if _EDIT_PATTERNS.search(text):
            return GoalRouterModel(route="goal_manage", manage_action="edit", tools=[])
        if _SCHEDULE_PATTERNS.search(text):
            return GoalRouterModel(
                route="goal_schedule_read",
                manage_action="none",
                tools=[{"tool": "get_schedule_range"}, {"tool": "get_free_time"}],
            )
        return GoalRouterModel(route="goal_chat", manage_action="none", tools=[])

    if phase == "confirm":
        return GoalRouterModel(route="goal_confirm", manage_action="none", tools=[])

    if complete and (_CREATE_PATTERNS.search(text) or phase == "ready"):
        return GoalRouterModel(route="goal_create", manage_action="none", tools=[])

    if phase in ("interrogate", "intake", "clarify", ""):
        if complete and re.search(r"\b(yes|create|generate)\b", text, re.I):
            return GoalRouterModel(route="goal_create", manage_action="none", tools=[])
        if _SCHEDULE_PATTERNS.search(text) and not complete:
            return GoalRouterModel(
                route="goal_schedule_read",
                manage_action="none",
                tools=[{"tool": "get_free_time"}],
            )
        op = match_operational_intent(text)
        if op:
            return op
        if _DELETE_PATTERNS.search(text):
            return GoalRouterModel(
                route="goal_manage",
                manage_action="delete_goal",
                tools=[{"tool": "delete_goal"}],
            )
        if _EDIT_PATTERNS.search(text):
            return GoalRouterModel(route="goal_manage", manage_action="edit", tools=[])
        return GoalRouterModel(route="goal_interrogate", manage_action="none", tools=[])

    if phase == "creating":
        return GoalRouterModel(route="goal_chat", manage_action="none", tools=[])

    return GoalRouterModel(route="goal_interrogate", manage_action="none", tools=[])
