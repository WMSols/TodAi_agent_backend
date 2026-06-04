"""Build turn context and trim chat history for Groq."""

from __future__ import annotations

import re
from typing import Any

from todai.database.buckets import (
    chat_bucket_limits,
    chat_router_pull_limit,
    goal_bucket_limits,
    messages_for_llm,
)

_MAX_CHARS = 3500

_SPECIALIST_TURNS_CHAT = 5
_SPECIALIST_TURNS_SCHEDULE = 2
_SPECIALIST_TURNS_WRITE = 4
_SPECIALIST_CHARS_CHAT = 3500
_SPECIALIST_CHARS_SCHEDULE = 1400

_TIME_FRAGMENT = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|(?:am|pm)\s*to\s*\d|^\s*\d{1,2}\s*(?:am|pm)",
    re.I,
)


def groq_history_from_chat(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    limits = chat_bucket_limits()
    return messages_for_llm(messages, pull=limits.pull, max_chars=_MAX_CHARS)


def groq_specialist_history(
    messages: list[dict[str, Any]],
    route: str,
) -> list[dict[str, str]]:
    """Trim history for specialist only — schedule routes get shorter context."""
    limits = chat_bucket_limits()
    is_chat = route == "chat"
    if route == "schedule_write":
        max_turns = min(_SPECIALIST_TURNS_WRITE, limits.pull)
    else:
        max_turns = (
            min(_SPECIALIST_TURNS_CHAT, limits.pull)
            if is_chat
            else min(_SPECIALIST_TURNS_SCHEDULE, limits.pull)
        )
    max_chars = _SPECIALIST_CHARS_CHAT if is_chat else _SPECIALIST_CHARS_SCHEDULE
    return messages_for_llm(messages, pull=max_turns, max_chars=max_chars)


def groq_goal_planner_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    limits = goal_bucket_limits()
    return messages_for_llm(messages, pull=limits.pull, max_chars=_MAX_CHARS)


def _prior_history_excluding_current(
    history: list[dict[str, str]],
    current_message: str,
) -> list[dict[str, str]]:
    if not history:
        return []
    last = history[-1]
    cur = (current_message or "").strip()
    if last.get("role") == "user" and (last.get("content") or "").strip() == cur:
        return history[:-1]
    return history


def groq_router_context(
    history: list[dict[str, str]],
    current_message: str,
) -> list[dict[str, str]]:
    """
    Up to N prior user messages for the router (follow-up intent/times).
    Optionally includes the last assistant line when clarifying or time-only replies.
    """
    prior = _prior_history_excluding_current(history, current_message)
    user_rows = [m for m in prior if m.get("role") == "user"]
    selected = user_rows[-chat_router_pull_limit():]
    if not selected:
        return []
    ctx: list[dict[str, str]] = []
    for m in selected:
        text = (m.get("content") or "").strip()
        if text:
            ctx.append({"role": "user", "content": text})
    msg = (current_message or "").strip()
    if prior and prior[-1].get("role") == "assistant":
        ac = (prior[-1].get("content") or "").strip()
        if ac and (
            _TIME_FRAGMENT.search(msg)
            or (len(msg) <= 80 and "?" in ac)
        ):
            ctx.append({"role": "assistant", "content": ac[:500]})
    return ctx


def merged_write_context_message(messages: list[dict[str, Any]], current: str) -> str:
    """Combine recent user lines for short write follow-ups (times after an add)."""
    cur = (current or "").strip()
    if len(cur) > 80:
        return cur
    users = [
        (m.get("content") or "").strip()
        for m in messages
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    if len(users) <= 1:
        return cur
    parts = users[-3:]
    if parts[-1] != cur:
        parts.append(cur)
    else:
        parts = parts[-3:]
    if len(parts) <= 1:
        return cur
    return " | ".join(parts)


def assistant_meta(trace: list[dict[str, Any]], schedule_display: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"tool_trace": trace, **extra}
    if schedule_display:
        meta["schedule_display"] = schedule_display
    return meta
