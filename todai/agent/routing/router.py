"""
router.py — call the small router LLM and parse route + tool plan
"""

from __future__ import annotations

from typing import Any

from todai.agent.planner.llm import RouterOutput, parse_router_output, route_turn
from todai.agent.core.context import groq_router_context


def run_router(
    *,
    current_message: str,
    history: list[dict[str, str]],
    server_snapshot: dict[str, Any],
    conversation: dict[str, Any],
    date_anchor: dict[str, Any] | None = None,
) -> tuple[RouterOutput | None, list[dict[str, Any]], dict[str, Any] | None]:
    routing_context = groq_router_context(history, current_message)
    raw = route_turn(
        current_message=current_message,
        routing_context=routing_context or None,
        server_snapshot=server_snapshot,
        conversation=conversation,
        date_anchor=date_anchor,
    )
    router_dbg = raw.pop("_groq_debug", None) if isinstance(raw, dict) else None
    out, errs = parse_router_output(raw if isinstance(raw, dict) else {})
    return out, errs, router_dbg
