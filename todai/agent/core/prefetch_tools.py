"""
prefetch_tools.py — align router read tools with preview question type (schedule / free days / free time).
"""

from __future__ import annotations

from typing import Any

from todai.agent.routing.preview_range import PreviewRange, apply_preview_range_to_tools
from todai.agent.routing.preview_read_kind import PreviewReadKind, classify_preview_read

_SPECIALTY_TOOLS = frozenset({"get_free_time", "get_days_without_schedule"})


def augment_preview_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    message: str,
    scope: PreviewRange,
) -> list[dict[str, Any]]:
    """Ensure the right read tool(s) for free-day vs free-time vs normal schedule preview."""
    kind = classify_preview_read(message)
    want = {"from": scope.date_from, "to": scope.date_to}
    calls = apply_preview_range_to_tools(tool_calls, scope)

    def _drop(tools: frozenset[str]) -> list[dict[str, Any]]:
        return [c for c in calls if c.get("tool") not in tools]

    def _with_required(required: list[str]) -> list[dict[str, Any]]:
        out = list(calls)
        present = {str(c.get("tool")) for c in out}
        for tool in required:
            if tool not in present:
                out.append({"tool": tool, "arguments": dict(want)})
        return apply_preview_range_to_tools(out, scope)

    if kind == PreviewReadKind.FREE_DAYS:
        calls = _drop(frozenset({"get_free_time"}))
        return _with_required(["get_days_without_schedule", "get_schedule_range"])
    if kind == PreviewReadKind.FREE_TIME:
        calls = _drop(frozenset({"get_days_without_schedule"}))
        return _with_required(["get_free_time", "get_schedule_range"])
    calls = _drop(_SPECIALTY_TOOLS)
    return _with_required(["get_schedule_range"])
