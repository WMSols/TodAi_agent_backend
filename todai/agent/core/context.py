"""
context.py — build turn context and trim chat history for Groq
"""

from __future__ import annotations

import re
from typing import Any

_MAX_HISTORY = 5
_MAX_CHARS = 3500
_ROUTER_CONTEXT_MAX = 2

# Specialist history (token-opt): fewer turns for schedule routes
_SPECIALIST_TURNS_CHAT = 5
_SPECIALIST_TURNS_SCHEDULE = 2
_SPECIALIST_CHARS_CHAT = 3500
_SPECIALIST_CHARS_SCHEDULE = 1400

_TIME_FRAGMENT = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|(?:am|pm)\s*to\s*\d|^\s*\d{1,2}\s*(?:am|pm)",
    re.I,
)


def groq_history_from_chat(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = m.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS] + "\n…(truncated)"
        rows.append({"role": str(m["role"]), "content": text})
    return rows[-_MAX_HISTORY:]


def groq_specialist_history(
    messages: list[dict[str, Any]],
    route: str,
) -> list[dict[str, str]]:
    """Trim history for specialist only — schedule routes get shorter context."""
    is_chat = route == "chat"
    max_turns = _SPECIALIST_TURNS_CHAT if is_chat else _SPECIALIST_TURNS_SCHEDULE
    max_chars = _SPECIALIST_CHARS_CHAT if is_chat else _SPECIALIST_CHARS_SCHEDULE
    rows: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = m.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(truncated)"
        rows.append({"role": str(m["role"]), "content": text})
    return rows[-max_turns:]


def groq_router_context(
    history: list[dict[str, str]],
    current_message: str,
) -> list[dict[str, str]]:
    """
    Minimal prior turns for the router only (follow-up times / short replies).
    Full history stays on the specialist path.
    """
    msg = (current_message or "").strip()
    if len(msg) > 100 or not history or len(history) < 2:
        return []
    if _TIME_FRAGMENT.search(msg):
        return history[-_ROUTER_CONTEXT_MAX:]
    if len(msg) <= 48 and history[-1].get("role") == "assistant" and "?" in history[-1].get("content", ""):
        return history[-_ROUTER_CONTEXT_MAX:]
    return []


def assistant_meta(trace: list[dict[str, Any]], schedule_display: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"tool_trace": trace, **extra}
    if schedule_display:
        meta["schedule_display"] = schedule_display
    return meta
