"""
routing_guards.py — thin safety net after the router LLM (current message first)

  - Plain chat / acknowledgments → chat (no calendar panel)
  - Obvious add/remove → write / delete
  - Write follow-up times after schedule_write
  - normalize_router_tool_calls — flat from/to → arguments
"""

from __future__ import annotations

import re
from typing import Any

from todai.agent.planner.llm import AgentRoute, RouterOutput
from todai.agent.routing.preview_range import strip_router_tool_dates

_WRITE_VERBS = re.compile(r"\b(add|book|create|reschedule)\b", re.I)
_DELETE_VERBS = re.compile(r"\b(remove|delete|cancel|clear)\b", re.I)
_SCHEDULE_ON = re.compile(r"\bschedule\b.+\b(on|for)\b", re.I)
_SAME_ON_DAY = re.compile(r"\bsame\b.+\b(on|for)\b", re.I)
_PREVIEW_READ = re.compile(
    r"\b(?:give\s+(?:me\s+)?|show\s+(?:me\s+)?|what'?s\s+on|preview|"
    r"can you give|updated\s+com|upcoming\s+schedul|my\s+coming\s+schedul)",
    re.I,
)
_SCHEDULE_QUESTION = re.compile(
    r"\b(?:what|which|when)\b.+\b(?:my|the)\s+schedul",
    re.I,
)
_WHAT_MY_SCHEDULE = re.compile(
    r"\bwhat\b.*\b(?:is|are)\b.*\b(?:my|the)\b.*\bsch[ae]?du\w*",
    re.I,
)
_SCHEDULE_WORD = re.compile(r"\bsch[ae]?du\w*\b", re.I)
_DAY_WORD = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|today)\b",
    re.I,
)
_TIME_FRAGMENT = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|(?:am|pm)\s*to\s*\d|\b\d{1,2}\s*(?:am|pm)",
    re.I,
)
_ACK_PHRASES = re.compile(
    r"^(?:okay|ok|thanks|thank you|thx|that'?s good|thats good|sounds good|"
    r"perfect|great|nice|cool|sure|got it|alright|awesome)(?:\s|[,.!]|$)",
    re.I,
)
_CAPABILITY = re.compile(
    r"\b(?:what can you do|how can you help|what do you do|who are you|how are you)\b",
    re.I,
)
_GENERAL_CHAT = re.compile(
    r"\blet me ask\b|\bbest routine\b|\bgeneral (?:advice|question)\b",
    re.I,
)


def _norm(message: str) -> str:
    m = re.sub(r"[!?.…,]+$", "", (message or "").strip().lower())
    return " ".join(m.split())


def normalize_router_tool_calls(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for call in tools:
        if not isinstance(call, dict):
            continue
        c = dict(call)
        args = dict(c.get("arguments") or {})
        for key in ("from", "to"):
            if key in c and key not in args:
                args[key] = c.pop(key)
        c["arguments"] = args
        out.append(c)
    return out


def is_schedule_delete_message(message: str) -> bool:
    m = _norm(message)
    if not _DELETE_VERBS.search(m):
        return False
    return not _WRITE_VERBS.search(m)


_MONTH_READ = re.compile(
    r"\b(?:next|last|previous)\s+month\b|\bmonth\s+of\s+|\b(?:in|for)\s+"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)


def _strip_leading_ack(message: str) -> str:
    return re.sub(
        r"^(?:okay|ok|thanks|thank you|thx)[,\s]+",
        "",
        (message or "").strip(),
        flags=re.I,
    ).strip()


def is_schedule_preview_read(message: str) -> bool:
    """View schedule (not add/remove) — e.g. what is my schedule on Friday or next month."""
    for raw in (message, _strip_leading_ack(message)):
        m = _norm(raw)
        if not m:
            continue
        if is_schedule_delete_message(raw) or _WRITE_VERBS.search(m):
            continue
        if _PREVIEW_READ.search(m):
            return True
        if _WHAT_MY_SCHEDULE.search(m):
            return True
        if _SCHEDULE_QUESTION.search(m) and (_DAY_WORD.search(m) or _MONTH_READ.search(m)):
            return True
        if re.search(r"\bwhat\b.+\bsch[ae]?du\w*", m) and (_DAY_WORD.search(m) or _MONTH_READ.search(m)):
            return True
        if re.search(r"\b(?:what|which|show)\b.+\bsch[ae]?du\w*", m) and _MONTH_READ.search(m):
            return True
        if _MONTH_READ.search(m) and _SCHEDULE_WORD.search(m):
            return True
        if _MONTH_READ.search(m) and re.search(
            r"\b(?:what|which|show|have|got)\b.+\b(?:for|in)\b", m
        ):
            return True
    return False


def is_schedule_write_message(message: str) -> bool:
    m = _norm(message)
    if is_schedule_preview_read(message):
        return False
    if _WRITE_VERBS.search(m):
        if _PREVIEW_READ.search(m) and not re.search(r"\badd\b", m, re.I):
            return False
        return True
    if _SAME_ON_DAY.search(m):
        return True
    if _SCHEDULE_ON.search(m):
        return not _PREVIEW_READ.search(m)
    return False


def is_plain_chat_message(message: str) -> bool:
    """Social / ack / general question — not asking to view or edit the calendar."""
    m = _norm(message)
    if not m:
        return True
    if is_schedule_write_message(message) or is_schedule_delete_message(message):
        return False
    if is_schedule_preview_read(message):
        return False
    if _PREVIEW_READ.search(m):
        return False
    if re.search(r"\bwhat\b.+\b(?:on|for)\b", m) and re.search(
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today)\b", m
    ):
        return False
    if re.match(r"^okay,\s*thats good$", m):
        return True
    if _ACK_PHRASES.match(m):
        rest = _strip_leading_ack(message)
        if not rest or len(_norm(rest)) < 4:
            return True
    if _CAPABILITY.search(m) or _GENERAL_CHAT.search(m):
        return True
    if m in {"hey", "hi", "hello", "yo", "sup"}:
        return True
    return False


# Tests / older callers
is_general_chat_message = is_plain_chat_message


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
    r"\b(?:what'?s on|what\s+are|show|view|preview|my)\b.*\b(?:schedule|calendar|week)\b|"
    r"\bschedule\b.*\b(?:week|today|tomorrow)\b|"
    r"\bwhat\b.*\b(?:is|are)\b.*\b(?:my|the)\b.*\bschedul",
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


def is_write_followup(message: str, last_agent_mode: str | None) -> bool:
    if (last_agent_mode or "") not in ("schedule_write",):
        return False
    m = _norm(message)
    if is_schedule_write_message(message) or is_schedule_delete_message(message):
        return True
    if is_schedule_preview_read(message):
        return False
    if _PREVIEW_READ.search(m):
        return False
    return bool(_TIME_FRAGMENT.search(m))


def apply_route_guards(
    message: str,
    router_out: RouterOutput,
    *,
    last_agent_mode: str | None = None,
) -> tuple[AgentRoute, list[dict[str, Any]], list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    tools = strip_router_tool_dates(normalize_router_tool_calls(router_out.tools))
    route = router_out.agent_route

    if should_redirect_to_goal_planner(message):
        notes.append(
            {
                "phase": "route_guard",
                "forced": "chat",
                "reason": "goal_planner_redirect",
                "was": route.value,
            }
        )
        return AgentRoute.CHAT, [], notes

    if is_plain_chat_message(message):
        if route != AgentRoute.CHAT:
            notes.append({"phase": "route_guard", "forced": "chat", "reason": "plain_chat", "was": route.value})
        return AgentRoute.CHAT, [], notes

    if is_schedule_delete_message(message):
        if route != AgentRoute.SCHEDULE_DELETE:
            notes.append(
                {"phase": "route_guard", "forced": "schedule_delete", "reason": "delete_intent", "was": route.value}
            )
        return AgentRoute.SCHEDULE_DELETE, tools, notes

    if is_schedule_preview_read(message):
        if route != AgentRoute.SCHEDULE_PREVIEW:
            notes.append(
                {"phase": "route_guard", "forced": "schedule_preview", "reason": "preview_read", "was": route.value}
            )
        return AgentRoute.SCHEDULE_PREVIEW, tools, notes

    if is_schedule_write_message(message) or is_write_followup(message, last_agent_mode):
        if route != AgentRoute.SCHEDULE_WRITE:
            reason = "write_followup" if is_write_followup(message, last_agent_mode) else "write_intent"
            notes.append({"phase": "route_guard", "forced": "schedule_write", "reason": reason, "was": route.value})
        return AgentRoute.SCHEDULE_WRITE, tools, notes

    return route, tools, notes
