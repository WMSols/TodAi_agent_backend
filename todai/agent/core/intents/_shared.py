"""
_shared.py — specialist LLM + optional calendar apply (used by all intents)
"""

from __future__ import annotations

from typing import Any

from todai.agent.planner.llm import parse_specialist_output, specialist_turn
from todai.agent.core.types import TurnContext


def run_specialist(ctx: TurnContext) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    raw = specialist_turn(
        route=ctx.route,
        history=ctx.history,
        server_snapshot=ctx.server_snapshot,
        date_anchor=ctx.date_anchor,
        highlights=ctx.highlights,
        read_results=ctx.read_results,
        preview_range=ctx.preview_range.as_dict() if ctx.preview_range else None,
        current_message=ctx.message,
        full_index=ctx.full_index,
        last_agent_mode=ctx.conversation.get("last_agent_mode"),
    )
    spec_dbg = raw.pop("_groq_debug", None) if isinstance(raw, dict) else None
    if spec_dbg and isinstance(spec_dbg, dict):
        ctx.trace.append({"phase": "prompt_bundle", "specialist": spec_dbg.get("prompt_chars"), "bundle": spec_dbg.get("prompt_bundle")})
    return (*parse_specialist_output(raw if isinstance(raw, dict) else {}), spec_dbg)
