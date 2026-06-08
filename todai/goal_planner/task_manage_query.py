"""Parse delete/manage intents for goal tasks (day, single task, clarify)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from todai.goal_planner.task_query import (
    _DELETE_VERB,
    _match_tasks_by_title,
    filter_tasks_by_dates,
    parse_day_dates_in_message,
    resolve_task_ordinal,
)

DeleteAction = Literal[
    "none",
    "delete_day",
    "delete_task",
    "delete_plan",
    "delete_goal",
    "delete_all",
    "clarify",
]

_DELETE_ALL = re.compile(
    r"\b(?:delete|remove|clear)\b.*\b(?:all|every)\b.*\b(?:goals?|plans?)\b|"
    r"\b(?:delete|remove|clear)\b.*\b(?:my\s+)?goals\b",
    re.I,
)
_DELETE_GOAL = re.compile(
    r"\b(?:delete|remove|cancel|clear|drop)\b.*\b(?:my\s+)?goal\b|"
    r"\b(?:my\s+)?goal\b.*\b(?:delete|remove)\b",
    re.I,
)
_DELETE_PLAN_WEEK = re.compile(
    r"\b(?:delete|remove|clear)\b.*\b(?:tasks?\s+only|only\s+tasks?)\b|"
    r"\b(?:delete|remove|clear)\b.*\b(?:all|entire|whole|full)\s+(?:week|plan)\s*tasks?\b|"
    r"\breset\b.*\b(?:plan|tasks?|draft)\b|"
    r"\b(?:delete|remove)\b.*\bdraft\b|"
    r"\b(?:delete|remove)\b.*\b(?:7[- ]?day|week)\s+plan\b",
    re.I,
)
_DELETE_TASKS_VAGUE = re.compile(
    r"\b(?:delete|remove|clear|drop)\b.*\b(?:my\s+)?tasks?\b|"
    r"\b(?:my\s+)?tasks?\b.*\b(?:delete|remove|clear|drop)\b",
    re.I,
)
_HAS_WEEKDAY = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.I,
)


@dataclass(frozen=True)
class DeleteManageIntent:
    action: DeleteAction
    dates: tuple[str, ...] = ()
    day_label: str = ""
    tasks: tuple[dict[str, Any], ...] = ()
    clarify_message: str = ""


def _day_label(dates: list[date]) -> str:
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0].strftime("%A, %d %b")
    return ", ".join(d.strftime("%a %d %b") for d in dates)


def parse_delete_manage_intent(
    message: str,
    *,
    start: date,
    end: date,
    all_tasks: list[dict[str, Any]],
) -> DeleteManageIntent:
    text = (message or "").strip()
    if not text or not _DELETE_VERB.search(text):
        return DeleteManageIntent(action="none")

    if _DELETE_ALL.search(text):
        return DeleteManageIntent(action="delete_all")

    if _DELETE_GOAL.search(text) and not _HAS_WEEKDAY.search(text):
        return DeleteManageIntent(action="delete_goal")

    day_dates = parse_day_dates_in_message(text, start=start, end=end)
    if day_dates:
        iso = tuple(d.isoformat() for d in day_dates)
        day_tasks = filter_tasks_by_dates(all_tasks, iso)
        one = resolve_task_ordinal(text, day_tasks)
        if one:
            return DeleteManageIntent(
                action="delete_task",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=(one,),
            )
        if day_tasks and (_DELETE_TASKS_VAGUE.search(text) or _HAS_WEEKDAY.search(text)):
            return DeleteManageIntent(
                action="delete_day",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=tuple(day_tasks),
            )

    matched = _match_tasks_by_title(text, all_tasks)
    if matched and len(matched) <= 3:
        return DeleteManageIntent(
            action="delete_task",
            tasks=tuple(matched[:1]),
        )

    if _DELETE_PLAN_WEEK.search(text) and not _HAS_WEEKDAY.search(text):
        if not all_tasks:
            return DeleteManageIntent(action="delete_goal")
        return DeleteManageIntent(action="delete_plan")

    if _DELETE_TASKS_VAGUE.search(text):
        if not all_tasks:
            return DeleteManageIntent(action="delete_goal")
        return DeleteManageIntent(
            action="clarify",
            clarify_message=(
                "Which tasks should I remove?\n\n"
                "• **One day** — e.g. *remove Tuesday tasks*\n"
                "• **One task** — e.g. *delete the first task on Friday*\n"
                "• **Whole week** — say *delete all plan tasks* or *reset my week*\n"
                "• **Entire goal** — say *delete my goal*"
            ),
        )

    if _DELETE_VERB.search(text):
        return DeleteManageIntent(action="delete_goal")

    return DeleteManageIntent(action="none")
