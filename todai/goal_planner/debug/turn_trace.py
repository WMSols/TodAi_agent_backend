"""Per-turn Groq call trace (in-memory, scoped to one goal plan message)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_turn_groq_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "goal_turn_groq_calls",
    default=None,
)


def begin_goal_turn_trace() -> None:
    _turn_groq_calls.set([])


def clear_goal_turn_trace() -> None:
    _turn_groq_calls.set(None)


def get_turn_groq_calls() -> list[dict[str, Any]]:
    calls = _turn_groq_calls.get()
    return list(calls or [])


def _copy_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Store full messages for debug UI (no truncation)."""
    out: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        out.append({"role": role, "content": content})
    return out


def _copy_response(result: dict[str, Any]) -> Any:
    """Store full Groq JSON response for debug UI (strip internal _ keys)."""
    return {k: v for k, v in result.items() if not str(k).startswith("_")}


def record_groq_call(
    *,
    phase: str,
    messages: list[dict[str, str]],
    result: dict[str, Any],
    override_applied: bool = False,
) -> None:
    calls = _turn_groq_calls.get()
    if calls is None:
        return
    dbg = result.get("_groq_debug") if isinstance(result, dict) else None
    calls.append(
        {
            "phase": phase,
            "override_applied": override_applied,
            "messages": _copy_messages(messages),
            "response": _copy_response(result if isinstance(result, dict) else {}),
            "ok": bool(isinstance(dbg, dict) and dbg.get("ok")),
            "groq_debug": dbg if isinstance(dbg, dict) else None,
        }
    )
