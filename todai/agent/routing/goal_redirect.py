"""Detect goal-planner ownership — calendar agent redirects, does not manage goals."""

from __future__ import annotations

import re

_GOALS_LIST = re.compile(
    r"\b(review goals?|my goals?|list goals?|show goals?|view goals?|all goals?|"
    r"existing goals?|what are my goals?)\b",
    re.I,
)
_GOAL_PROGRESS = re.compile(r"\bwhat\b.*\bgoals?\b", re.I)
_GOAL_DELETE = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(goal|goals|plan|tasks?)\b|"
    r"\b(goal|goals|plan|tasks?)\b.*\b(delete|remove|cancel|clear)\b",
    re.I,
)
_GOAL_SETUP = re.compile(
    r"\b(create|generate|build|make|add|set\s*up|start)\b.*\b(tasks?|plan|schedule|goal)\b|"
    r"\b(tasks?|plan)\b.*\b(for|to)\b.*\b(this|my|the)?\s*goal\b|"
    r"\bnew\s+goal\b|\bgoal\s+plan\b|\b7[- ]?day\b.*\b(plan|goal)\b|"
    r"\b(confirm|okay|yes)\b.*\b(plan|tasks?)\b",
    re.I,
)
_GOAL_TASKS_FOR_GOAL = re.compile(
    r"\b(task|tasks)\b.*\b(this|my|the)\s+goal\b|"
    r"\bgoal\b.*\b(task|tasks)\b",
    re.I,
)
_GOAL_INTAKE = re.compile(
    r"\b(objective|difficulty|tasks per day|minutes per day|hours daily|days per week)\b",
    re.I,
)
_COMBINED_SCHEDULE = re.compile(
    r"\b(?:what'?s on|show|view|preview|my)\b.*\b(?:schedule|calendar|week)\b|"
    r"\bschedule\b.*\b(?:week|today|tomorrow)\b",
    re.I,
)

GOAL_PLANNER_REDIRECT_REPLY = (
    "Goal plans (create, edit, delete, and 7-day tasks) live in the **Goal planner** tab. "
    "Open **Goal planner** → **My goals** or **New goal**.\n\n"
    "Here in **Calendar**, I can show your **combined week** (events + goal tasks) — "
    "e.g. *what's on my schedule this week*."
)


def should_redirect_to_goal_planner(message: str) -> bool:
    """
    True when the user is asking for goal lifecycle / management, not a calendar-only view.
    Combined schedule questions stay on the calendar agent (with goal overlay).
    """
    text = (message or "").strip()
    if not text:
        return False
    if _COMBINED_SCHEDULE.search(text) and not _GOAL_DELETE.search(text):
        if _GOALS_LIST.search(text) and _COMBINED_SCHEDULE.search(text):
            return False
        return False
    if _GOALS_LIST.search(text) or _GOAL_PROGRESS.search(text):
        return True
    if _GOAL_DELETE.search(text):
        return True
    if _GOAL_SETUP.search(text):
        return True
    if _GOAL_TASKS_FOR_GOAL.search(text):
        return True
    if _GOAL_INTAKE.search(text) and re.search(r"\bgoal\b", text, re.I):
        return True
    if re.search(r"\bgoal\s+planner\b", text, re.I):
        return True
    return False
