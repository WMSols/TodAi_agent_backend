"""
router.py — goal planner routing (Groq + rules fallback + guards)

Single module for intent classification: contracts, regex rules, Groq router,
history context for specialists, and route_goal_turn entry point.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from todai.agent.core.context import _prior_history_excluding_current
from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.database.buckets import goal_bucket_limits, messages_for_llm
from todai.goal_planner.interrogation import answers_complete, is_confirm_settings_edit


# --- Router contracts ---





GoalRoute = Literal[
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_manage",
    "goal_schedule_read",
    "goal_tasks_summary",
    "goal_chat",
]

ManageAction = Literal[
    "none",
    "list",
    "delete_plan",
    "delete_goal",
    "delete_all",
    "delete_day",
    "delete_task",
    "edit",
]

_VALID_ROUTES = {
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_manage",
    "goal_schedule_read",
    "goal_tasks_summary",
    "goal_chat",
    # legacy aliases from rules router
    "goal_goals_list",
    "goal_delete",
    "goal_edit",
}

_MANAGE_ACTIONS = {
    "none",
    "list",
    "delete_plan",
    "delete_goal",
    "delete_all",
    "delete_day",
    "delete_task",
    "edit",
}

_GOAL_TOOLS = frozenset(
    {
        "list_goals",
        "list_goals_with_progress",
        "get_plan_detail",
        "delete_plan",
        "delete_goal",
        "delete_all_goals",
        "get_schedule_range",
        "get_free_time",
    }
)


class GoalRouteEnum(str, Enum):
    INTERROGATE = "goal_interrogate"
    CONFIRM = "goal_confirm"
    CREATE = "goal_create"
    MANAGE = "goal_manage"
    SCHEDULE_READ = "goal_schedule_read"
    TASKS_SUMMARY = "goal_tasks_summary"
    CHAT = "goal_chat"


class GoalRouterModel(BaseModel):
    """Parsed Groq router JSON."""

    route: str = "goal_chat"
    manage_action: str = "none"
    tools: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("route", mode="before")
    @classmethod
    def _norm_route(cls, v: Any) -> str:
        s = str(v or "goal_chat").strip().lower()
        if s in ("goal_goals_list", "goal_list"):
            return "goal_manage"
        if s in ("goal_delete",):
            return "goal_manage"
        if s in ("goal_edit",):
            return "goal_manage"
        return s if s in _VALID_ROUTES else "goal_chat"

    @field_validator("manage_action", mode="before")
    @classmethod
    def _norm_action(cls, v: Any) -> str:
        s = str(v or "none").strip().lower()
        if s in ("delete", "remove"):
            return "delete_goal"
        if s in ("delete_goal", "remove_goal"):
            return "delete_goal"
        if s in ("delete_all", "remove_all", "clear_all"):
            return "delete_all"
        if s in ("list", "review", "show", "progress"):
            return "list"
        return s if s in _MANAGE_ACTIONS else "none"

    @field_validator("tools", mode="before")
    @classmethod
    def _norm_tools(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if isinstance(v, str):
            return [{"tool": v.strip()}] if v.strip() else []
        if not isinstance(v, list):
            return []
        out: list[dict[str, Any]] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append({"tool": item.strip()})
            elif isinstance(item, dict) and item.get("tool"):
                out.append({"tool": str(item["tool"]).strip(), "arguments": item.get("arguments") or {}})
        return out


def normalize_router_tools(tools: list[dict[str, Any]] | str | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    if isinstance(tools, str):
        tools = [tools]
    out: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, str) and t.strip():
            name = t.strip()
        elif isinstance(t, dict):
            name = str(t.get("tool", "")).strip()
        else:
            continue
        if name in _GOAL_TOOLS:
            args = t.get("arguments") or {} if isinstance(t, dict) else {}
            out.append({"tool": name, "arguments": args})
    return out


_LEGACY_MANAGE = {
    "goal_goals_list": "list",
    "goal_list": "list",
    "goal_delete": "delete_goal",
    "goal_edit": "edit",
}


def parse_goal_router_output(raw: dict[str, Any]) -> tuple[GoalRouterModel | None, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return None, [{"code": "INVALID_GOAL_ROUTER", "detail": "expected object"}]
    dbg = raw.get("_groq_debug")
    if isinstance(dbg, dict) and dbg.get("ok") is False:
        return None, [{"code": "GROQ_ROUTER_FAILED", "detail": dbg}]
    reply = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
    has_route = bool(raw.get("route") or raw.get("intent"))
    if reply and not has_route and re.search(r"rate limit|groq http", reply, re.I):
        return None, [{"code": "GROQ_RATE_LIMIT", "detail": reply[:200]}]
    raw_route = str(raw.get("route") or raw.get("intent") or "").strip().lower()
    manage_action = raw.get("manage_action") or raw.get("action") or "none"
    if str(manage_action).strip().lower() == "none" and raw_route in _LEGACY_MANAGE:
        manage_action = _LEGACY_MANAGE[raw_route]
    normalized = {
        "route": raw_route or raw.get("route") or raw.get("intent"),
        "manage_action": manage_action,
        "tools": normalize_router_tools(raw.get("tools") or raw.get("tool_plan")),
    }
    try:
        return GoalRouterModel.model_validate(normalized), []
    except Exception as e:
        return None, [{"code": "INVALID_GOAL_ROUTER", "detail": str(e)}]

# --- Rules fallback ---



import re


_TASK_SUMMARY_PATTERNS = re.compile(
    r"\b(what\b.*\b(tasks?|todos?)\b|"
    r"\bwhat\b.*\bprogress\b|"
    r"\bprogress\b.*\b(?:for|on|of|with)\b.*\b(?:this|my|the|current)?\s*(?:goal|plan)\b|"
    r"\b(?:this|my|the|current)\s+(?:goal|plan)\b.*\bprogress\b|"
    r"\b(tasks?|todos?)\b.*\b(for|on|in)\b.*\b(this|my|the)?\s*(goal|plan)\b|"
    r"\b(show|list|view|give)\b.*\b(my\s+)?(tasks?|plan)\b|"
    r"\b(my\s+)?(tasks?|daily\s+tasks?)\b.*\b(summary|overview|list)\b|"
    r"\b(brief|short)\b.*\b(summary|overview)\b|"
    r"\bshow\s+my\s+plan\b)",
    re.I,
)
_PROGRESS_QUERY = re.compile(
    r"\b(progress|how\s+(?:much|many)\s+(?:done|completed)|percent(?:age)?|done\s+so\s+far)\b",
    re.I,
)
_ALL_GOALS_PROGRESS = re.compile(r"\b(?:all|every)\s+(?:my\s+)?goals?\b", re.I)
_SCHEDULE_PATTERNS = re.compile(
    r"\b(free time|free slot|calendar|schedule|what.?s on|busy|available|"
    r"my schedule|view my calendar|this week|tomorrow)\b",
    re.I,
)
_GOALS_LIST_PATTERNS = re.compile(
    r"\b(review goals?|my goals?|list goals?|show goals?|view goals?|all goals?)\b",
    re.I,
)
_DAY_SCOPED_TASK_OR_GOAL = re.compile(
    r"\b(?:goals?|tasks?)\b.*\b(?:for\s+)?(?:this\s+)?(?:day|today)\b|"
    r"\b(?:for\s+)?(?:this\s+)?(?:day|today)\b.*\b(?:goals?|tasks?)\b|"
    r"\bwhat\b.*\b(?:are|is)\b.*\b(?:my\s+)?(?:goals?|tasks?)\b.*\b(?:for\s+)?(?:this\s+)?(?:day|today)\b",
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
_DELETE_ALL_GOALS_PHRASE = re.compile(
    r"\b(delete|remove|clear|drop)\b.*\b(?:my\s+)?goals\b|"
    r"\b(?:my\s+)?goals\b.*\b(delete|remove|clear|drop)\b",
    re.I,
)
# Plan edits on active plans only — NOT intake answers like "days to skip" (bare "skip" removed).
_EDIT_PATTERNS = re.compile(
    r"\b(move|reschedule|edit task|swap|easier|harder|mark done|complete task)\b|"
    r"\bskip\s+(?:a\s+)?task\b",
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
    r"\b(delete|remove|clear)\b.*\b(tasks?\s+only|only\s+tasks?)\b|"
    r"\b(delete|remove|clear)\b.*\b(?:all|entire|whole|full)\s+(?:week|plan)\s*tasks?\b|"
    r"\breset\b.*\b(plan|tasks?|draft)\b|"
    r"\b(delete|remove)\b.*\bdraft\b|"
    r"\b(delete|remove)\b.*\b(?:7[- ]?day|week)\s+plan\b|"
    r"\bkeep\b.*\bgoal\b.*\b(delete|remove)\b",
    re.I,
)
_DELETE_DAY_PATTERNS = re.compile(
    r"\b(delete|remove|clear|drop)\b(?:\W+\w+){0,10}\b(?:monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b|"
    r"\b(delete|remove|clear|drop)\b(?:\W+\w+){0,10}\b(?:tasks?|todos?)\b(?:\W+\w+){0,8}\b(?:on|for)?\s*"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.I,
)
_DELETE_TASK_SPECIFIC = re.compile(
    r"\b(delete|remove|clear|drop)\b.*\b(?:first|second|third|fourth|1st|2nd|3rd|4th|last)\s+task\b|"
    r"\b(delete|remove|clear|drop)\b.*\btask\s*#?\s*\d{1,2}\b",
    re.I,
)
_GUIDANCE_PATTERNS = re.compile(
    r"\b(?:how\s+(?:do|can|should)|help\s+(?:me\s+)?(?:with|on)|guide|explain|elaborate|"
    r"walk\s+me\s+through|tips?\s+(?:for|on)|what\s+should\s+i)\b",
    re.I,
)
_GOAL_COACH_PATTERNS = re.compile(
    r"\b(?:difficult|hardest|challenging|toughest|harder|easy\s+day|easiest)\b.*\b(?:day|tasks?)\b|"
    r"\b(?:day|tasks?)\b.*\b(?:difficult|hardest|challenging|toughest|harder)\b|"
    r"\b(?:should\s+i|what\s+do\s+you\s+think|do\s+you\s+think|recommend|suggest)\b|"
    r"\b(?:on\s+track|am\s+i\s+doing|focus\s+on|priorit(?:y|ize)|busiest|lightest)\b|"
    r"\b(?:why\s+no\s+tasks?|empty\s+day|skip\s+day)\b|"
    r"\b(?:advice|guidance|coach|motivat)\b",
    re.I,
)
_DELETE_FULL_PLAN_PHRASE = re.compile(
    r"\b(delete|remove|cancel|clear|drop)\b.*\b(plan|program|programme)\b|"
    r"\b(delete|remove)\b.*\b(weight\s*loss|fitness|workout)\b",
    re.I,
)
_WHAT_GOALS_PATTERNS = re.compile(
    r"\bwhat\b.*\b(my\s+)?goals\b|\bwhich\s+goals?\b",
    re.I,
)
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
    from todai.database.utils.dates import is_today_question

    text = (message or "").strip()
    if not text:
        return None
    if is_today_question(text):
        return GoalRouterModel(route="goal_chat", manage_action="none", tools=[])
    if _DAY_SCOPED_TASK_OR_GOAL.search(text):
        return GoalRouterModel(route="goal_tasks_summary", manage_action="none", tools=[])
    if _DELETE_ALL_PATTERNS.search(text) or _DELETE_ALL_GOALS_PHRASE.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_all",
            tools=[{"tool": "delete_all_goals"}],
        )
    coach = match_goal_coach_intent(text)
    if coach:
        return coach
    if _DELETE_DAY_PATTERNS.search(text):
        return GoalRouterModel(route="goal_manage", manage_action="delete_day", tools=[])
    if _DELETE_TASK_SPECIFIC.search(text):
        return GoalRouterModel(route="goal_manage", manage_action="delete_task", tools=[])
    if _DELETE_PLAN_ONLY_PATTERNS.search(text) and not _DELETE_DAY_PATTERNS.search(text):
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
    if _TASK_SUMMARY_PATTERNS.search(text):
        return GoalRouterModel(route="goal_tasks_summary", manage_action="none", tools=[])
    if _PROGRESS_QUERY.search(text) and not _ALL_GOALS_PROGRESS.search(text):
        return GoalRouterModel(route="goal_tasks_summary", manage_action="none", tools=[])
    if _is_all_goals_list_query(text):
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


def match_intake_escape_intent(message: str) -> GoalRouterModel | None:
    """Explicit delete/cancel only — allowed to break incomplete intake."""
    text = (message or "").strip()
    if not text:
        return None
    if _DELETE_ALL_PATTERNS.search(text) or _DELETE_ALL_GOALS_PHRASE.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_all",
            tools=[{"tool": "delete_all_goals"}],
        )
    if _DELETE_PLAN_ONLY_PATTERNS.search(text) and not _DELETE_DAY_PATTERNS.search(text):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_plan",
            tools=[{"tool": "delete_plan"}],
        )
    if (
        _DELETE_GOAL_PATTERNS.search(text)
        or _DELETE_FULL_PLAN_PHRASE.search(text)
        or _DELETE_SHORT_PATTERNS.search(text)
    ):
        return GoalRouterModel(
            route="goal_manage",
            manage_action="delete_goal",
            tools=[{"tool": "delete_goal"}],
        )
    return None


def match_goal_manage_intent(message: str) -> GoalRouterModel | None:
    """Delete/list/manage intents that must work during intake and confirm (not only active)."""
    escape = match_intake_escape_intent(message)
    if escape:
        return escape
    op = match_operational_intent(message)
    if op and op.route == "goal_manage":
        return op
    return None


def match_goal_coach_intent(message: str) -> GoalRouterModel | None:
    """Coaching / analytical questions → grounded goal_chat (not explicit task list)."""
    text = (message or "").strip()
    if not text:
        return None
    if re.search(r"\b(delete|remove|clear|drop)\b", text, re.I):
        return None
    is_coach = bool(_GOAL_COACH_PATTERNS.search(text)) or (
        _GUIDANCE_PATTERNS.search(text)
        and not re.search(r"\b(delete|remove|clear|drop)\b", text, re.I)
    )
    if not is_coach:
        return None
    if _TASK_SUMMARY_PATTERNS.search(text) and not _GOAL_COACH_PATTERNS.search(text):
        return None
    return GoalRouterModel(route="goal_chat", manage_action="none", tools=[])


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
        if _TASK_SUMMARY_PATTERNS.search(text):
            return GoalRouterModel(route="goal_tasks_summary", manage_action="none", tools=[])
        if _PROGRESS_QUERY.search(text) and not _ALL_GOALS_PROGRESS.search(text):
            return GoalRouterModel(route="goal_tasks_summary", manage_action="none", tools=[])
        if _is_all_goals_list_query(text):
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
        op = match_goal_manage_intent(text)
        if op:
            return op
        return GoalRouterModel(route="goal_confirm", manage_action="none", tools=[])

    if complete and (_CREATE_PATTERNS.search(text) or phase == "ready"):
        return GoalRouterModel(route="goal_create", manage_action="none", tools=[])

    if phase in ("interrogate", "intake", "clarify", ""):
        if complete and re.search(r"\b(yes|create|generate)\b", text, re.I):
            return GoalRouterModel(route="goal_create", manage_action="none", tools=[])
        escape = match_intake_escape_intent(text)
        if escape:
            return escape
        return GoalRouterModel(route="goal_interrogate", manage_action="none", tools=[])

    if phase == "creating":
        return GoalRouterModel(route="goal_chat", manage_action="none", tools=[])

    return GoalRouterModel(route="goal_interrogate", manage_action="none", tools=[])

# --- Router guards ---



from typing import Any


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
    if route == "goal_tasks_summary":
        return []
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

    if _TASK_SUMMARY_PATTERNS.search((message or "").strip()) and out.route == "goal_schedule_read":
        out = out.model_copy(update={"route": "goal_tasks_summary", "manage_action": "none", "tools": []})
        notes.append({"phase": "router_guard", "reason": "task_summary_over_schedule_read"})

    coach = match_goal_coach_intent(message)
    if coach and out.route in ("goal_tasks_summary", "goal_schedule_read", "goal_manage"):
        if out.route != "goal_manage" or out.manage_action in ("none", "edit"):
            out = coach
            manage_action = "none"
            tools = []
            notes.append({"phase": "router_guard", "reason": "coach_over_list_or_schedule"})

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


def apply_goal_route_guards(
    out: GoalRouterModel,
    *,
    phase: str,
    answers: dict,
    ui_mode: str = "my_goals",
    message: str = "",
) -> tuple[GoalRouterModel, str]:
    """Return possibly adjusted router output and guard reason suffix."""
    complete = answers_complete(answers)
    reasons: list[str] = []
    manage_op = match_goal_manage_intent(message)

    if phase == "confirm":
        default_obj = str((answers.get("objective") or {}).get("parsed") or "")
        if is_confirm_settings_edit(message, default_objective=default_obj):
            out = out.model_copy(update={"route": "goal_confirm", "manage_action": "none"})
            return out, "confirm_settings_edit"
        if manage_op and manage_op.manage_action in (
            "delete_goal",
            "delete_plan",
            "delete_all",
            "delete_day",
            "delete_task",
        ):
            return manage_op, "delete_during_confirm"
        if out.route == "goal_manage":
            out = out.model_copy(update={"route": "goal_confirm", "manage_action": "none"})
            return out, "confirm_not_manage"
        if out.route != "goal_confirm":
            out = out.model_copy(update={"route": "goal_confirm", "manage_action": "none"})
            reasons.append("force_confirm_phase")
        return out, "|".join(reasons) if reasons else "ok"

    intake_only = ui_mode == "new_goal"
    if intake_only and phase in ("interrogate", "intake", "clarify", "") and not complete:
        escape = match_intake_escape_intent(message)
        if escape:
            return escape, "intake_explicit_delete"
        if out.route != "goal_interrogate" or out.manage_action != "none" or out.tools:
            out = out.model_copy(
                update={"route": "goal_interrogate", "manage_action": "none", "tools": []}
            )
            reasons.append("force_intake_phase")
        return out, "|".join(reasons) if reasons else "ok"

    if phase == "active" and out.route == "goal_interrogate":
        out = out.model_copy(update={"route": "goal_chat", "manage_action": "none"})
        reasons.append("active_not_interrogate")

    if phase == "creating":
        if manage_op or out.route == "goal_manage":
            return manage_op or out, "delete_during_creating"
        out = out.model_copy(update={"route": "goal_chat", "manage_action": "none"})
        reasons.append("creating_wait")

    return out, "|".join(reasons) if reasons else "ok"

# --- Groq router + routing context ---



import json
import re
from typing import Any


_ROUTER_PULL = 4
_ROUTER_CHARS = 1800
_MANAGE_PULL = 5
_MANAGE_CHARS = 2800
_CHAT_PULL = 6
_CHAT_CHARS = 3200

_CONFIRM_FRAGMENT = re.compile(
    r"^\s*(yes|no|yeah|yep|nope|ok|okay|sure|cancel|confirm|delete\s+it|go\s+ahead)\s*[!?.]*\s*$",
    re.I,
)
_CONFIRM_SHORT = re.compile(
    r"^\s*(yes|no|yeah|yep|nope|nah|ok|okay|sure|cancel|confirm|leave\s+it|don't|do\s+not)\b",
    re.I,
)


def should_hold_pending_manage(message: str) -> bool:
    """True only when the user is answering a pending delete confirm (yes/no), not a new question."""
    text = (message or "").strip()
    if not text:
        return False
    if _CONFIRM_FRAGMENT.match(text):
        return True
    if len(text) <= 48 and _CONFIRM_SHORT.match(text):
        if not re.search(r"\b(tasks?|goals?|today|what|show|list|calendar|schedule|progress)\b", text, re.I):
            return True
    return False


def _is_all_goals_list_query(text: str) -> bool:
    if _DAY_SCOPED_TASK_OR_GOAL.search(text):
        return False
    return bool(_WHAT_GOALS_PATTERNS.search(text) or _GOALS_LIST_PATTERNS.search(text))

GOAL_ROUTER_JSON_CONTRACT = (
    'JSON only: {"route": string, "manage_action": string, "tools": array}\n'
    "route: goal_interrogate | goal_confirm | goal_create | goal_manage | "
    "goal_tasks_summary | goal_schedule_read | goal_chat\n"
    "manage_action (route=goal_manage): list | delete_goal | delete_plan | delete_all | "
    "delete_day | delete_task | edit | none\n"
    'tools: [{"tool": string, "arguments": object}] — e.g. '
    '[{"tool":"list_goals_with_progress"},{"tool":"get_schedule_range","arguments":{}}]\n'
    "Goal tools: list_goals_with_progress, get_plan_detail, delete_goal, delete_plan, "
    "delete_all_goals, get_schedule_range, get_free_time\n"
    "Calendar read tools omit from/to dates (server fills plan window).\n"
)

GOAL_ROUTER_SYSTEM = (
    "TodAI goal-plan router. Route CURRENT_USER_MESSAGE using GOAL_CONTEXT + ROUTING_CONTEXT.\n"
    + GOAL_ROUTER_JSON_CONTRACT
    + "PHASE LOCKS (read GOAL_CONTEXT.phase + needs_task_setup first):\n"
    "- phase interrogate/intake + needs_task_setup: ALWAYS goal_interrogate. "
    "Setup answers (objective, tasks/day, skip days, none, no days to skip) are NOT manage/chat. "
    "ONLY exception: explicit delete goal/plan ('delete my goal', 'remove this plan').\n"
    "- phase confirm: goal_confirm (yes/no, edit settings). goal_create only if user confirms build. "
    "Explicit delete → goal_manage.\n"
    "- phase active + ui_mode my_goals: full routes below.\n"
    "DISAMBIGUATION:\n"
    "- goal_tasks_summary = task list OR progress for current/this goal (progress, % done, how many completed, show my plan, Wednesday tasks, tasks/goals for today/this day). tools [].\n"
    "- goal_manage list = ALL goals overview (list my goals, review goals) — NOT tasks for today or this goal. tools list_goals_with_progress.\n"
    "- goal_chat = COACHING (hardest days, how-to, tips, on track) OR what date/day is today — not a task table. tools [].\n"
    "- goal_schedule_read = calendar events + free time slots — NOT 'what date is today'.\n"
    "- goal_manage edit = move/reschedule/mark done on existing tasks — NOT intake skip-day answers.\n"
    "- phase active: progress/how much done → goal_tasks_summary, NEVER goal_interrogate.\n"
    "ROUTES:\n"
    "- goal_interrogate: setup Q&A (new_goal tab, no tasks yet).\n"
    "- goal_confirm: review summary; yes/no; inline setting changes.\n"
    "- goal_create: build tasks when answers complete + user confirms.\n"
    "- goal_manage: delete_goal | delete_plan | delete_day | delete_task | delete_all | list | edit.\n"
    "- goal_schedule_read: calendar + free time. tools: get_schedule_range, get_free_time.\n"
    "pending_manage in GOAL_CONTEXT: ONLY route goal_manage for short yes/no/cancel after a delete confirm.\n"
    "If user asks a NEW question (tasks today, what date, show plan, list tasks) → ignore pending_manage; route normally.\n"
    "Use ROUTING_CONTEXT only for short replies (yes after delete prompt → goal_manage).\n"
    "Output JSON only.\n"
)


def groq_goal_router_context(
    messages: list[dict[str, Any]],
    current_message: str,
    *,
    session: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """
    Prior turns for the goal router (mirrors calendar groq_router_context).

    Includes the last assistant line when the user is confirming, answering a
    question, or replying shortly after a manage/delete prompt.
    """
    limits = goal_bucket_limits()
    pull = min(_ROUTER_PULL, limits.pull)
    full = messages_for_llm(messages, pull=pull, max_chars=_ROUTER_CHARS)
    prior = _prior_history_excluding_current(full, current_message)

    user_rows = [m for m in prior if m.get("role") == "user"]
    selected = user_rows[-pull:]
    ctx: list[dict[str, str]] = []
    for m in selected:
        text = (m.get("content") or "").strip()
        if text:
            ctx.append({"role": "user", "content": text})

    msg = (current_message or "").strip()
    pending = (session or {}).get("pending_manage") or {}
    include_assistant = False
    if prior and prior[-1].get("role") == "assistant":
        ac = (prior[-1].get("content") or "").strip()
        if ac and (
            pending
            or _CONFIRM_FRAGMENT.match(msg)
            or (len(msg) <= 80 and "?" in ac)
            or (len(msg) <= 60 and re.search(r"\b(yes|no|delete|confirm)\b", msg, re.I))
        ):
            include_assistant = True
            ctx.append({"role": "assistant", "content": ac[:600]})

    if pending and not include_assistant and prior:
        last_a = next((m for m in reversed(prior) if m.get("role") == "assistant"), None)
        if last_a:
            ac = (last_a.get("content") or "").strip()
            if ac:
                ctx.append({"role": "assistant", "content": ac[:600]})

    return ctx


def groq_goal_manage_context(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    limits = goal_bucket_limits()
    pull = min(_MANAGE_PULL, limits.pull)
    return messages_for_llm(messages, pull=pull, max_chars=_MANAGE_CHARS)


def groq_goal_chat_context(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    limits = goal_bucket_limits()
    pull = min(_CHAT_PULL, limits.pull)
    return messages_for_llm(messages, pull=pull, max_chars=_CHAT_CHARS)


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
    from todai.goal_planner.interrogation import STEPS

    answers_complete_flag = all(
        isinstance(answers.get(k), dict) and answers[k].get("valid")
        for k in STEPS
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
            "server_today": session.get("server_today"),
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

# --- Public entry point ---





GoalRoute = Literal[
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_manage",
    "goal_schedule_read",
    "goal_tasks_summary",
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
    allow_groq: bool = True,
) -> GoalRouterOutput:
    """
    Classify one goal-plan turn (same layering as calendar: LLM → guards → handlers).

    Falls back to regex rules if Groq is off, rate-limited (allow_groq=False), or invalid JSON.
    """
    session = session or {}
    pending = session.get("pending_manage")
    if pending and should_hold_pending_manage(message):
        kind = str(pending.get("kind") or "")
        action = {
            "delete_all": "delete_all",
            "delete_plan": "delete_plan",
            "delete_goal": "delete_goal",
            "delete_day": "delete_day",
            "delete_task": "delete_task",
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

    model: GoalRouterModel | None = None
    errs: list[dict[str, Any]] = []
    dbg: dict[str, Any] | None = None
    source = "groq"

    if allow_groq and GROQ_API_KEY:
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
    elif not allow_groq:
        errs = [{"code": "RATE_LIMIT_PREFLIGHT", "detail": "rules_only"}]

    if model is None:
        if needs_task_setup:
            model = match_setup_intent(message, answers)
        if model is None:
            model = route_goal_turn_rules(message=message, phase=phase, answers=answers)
        source = "rules_fallback"
        if not allow_groq:
            reason = "rate_limit_preflight|rules_fallback"
        elif errs:
            reason = f"invalid_router|{'|'.join(e.get('code', '') for e in errs)}"
        else:
            reason = "rules_fallback"
    else:
        reason = "groq"
        manage_op = match_goal_manage_intent(message)
        in_intake = phase in ("interrogate", "intake", "clarify", "") and not answers_complete(
            answers
        )
        if manage_op and (
            model.route != "goal_manage"
            or (model.manage_action == "none" and manage_op.manage_action != "none")
        ):
            if not in_intake or manage_op.manage_action in (
                "delete_goal",
                "delete_plan",
                "delete_all",
            ):
                model = manage_op
                source = "rules_override"
        elif phase == "active":
            op = match_operational_intent(message)
            if op:
                if op.route == "goal_tasks_summary" and model.route != "goal_tasks_summary":
                    model = op
                    source = "rules_override"
                elif op.route == "goal_chat" and model.route in (
                    "goal_schedule_read",
                    "goal_manage",
                    "goal_tasks_summary",
                ):
                    model = op
                    source = "rules_override"
                elif op.route == "goal_manage" and model.route == "goal_chat":
                    model = op
                    source = "rules_override"

    model, guard_notes = apply_goal_router_guards(
        model,
        message=message,
        ui_mode=ui_mode,
        session=session,
        needs_task_setup=needs_task_setup,
    )

    model, phase_guard_reason = apply_goal_route_guards(
        model, phase=phase, answers=answers, ui_mode=ui_mode, message=message
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
