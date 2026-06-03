"""Trim goal-plan chat history for router / manage specialist."""

from __future__ import annotations

import re
from typing import Any

from todai.agent.core.context import _prior_history_excluding_current
from todai.database.buckets import goal_bucket_limits, messages_for_llm

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
