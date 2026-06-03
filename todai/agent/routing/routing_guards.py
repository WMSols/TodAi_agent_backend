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
from todai.agent.routing.goal_redirect import should_redirect_to_goal_planner
from todai.agent.routing.time_scope import strip_router_tool_dates

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
        if _SCHEDULE_QUESTION.search(m) and (_DAY_WORD.search(m) or _MONTH_READ.search(m)):
            return True
        if re.search(r"\bwhat\b.+\bschedul", m) and (_DAY_WORD.search(m) or _MONTH_READ.search(m)):
            return True
        if re.search(r"\b(?:what|which|show)\b.+\bschedul", m) and _MONTH_READ.search(m):
            return True
        if _MONTH_READ.search(m) and re.search(r"\bschedul", m):
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
