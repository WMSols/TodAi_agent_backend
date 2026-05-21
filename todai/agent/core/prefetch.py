"""
prefetch.py — validate router tool plan and execute read tools before intent handlers
"""

from __future__ import annotations

from datetime import date
from typing import Any

from todai.agent.tools.calendar import run_prefetch, validate_tool_plan
from todai.agent.planner.llm import AgentRoute, default_tools_for_route
from todai.agent.routing.preview_range import (
    PreviewRange,
    apply_preview_range_to_tools,
    clamp_preview_range,
    message_has_month_phrase,
    resolve_time_scope,
)
from todai.database.storage import UserStore, parse_server_date


def _single_day_range(iso: str) -> PreviewRange | None:
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return None
    return PreviewRange(
        date_from=d.isoformat(),
        date_to=d.isoformat(),
        label=d.strftime("%A, %d %B %Y"),
        granularity="day",
        explicit=True,
        fill_empty_days=True,
        show_free_banners=False,
    )


def _apply_time_scope(
    tool_calls: list[dict[str, Any]],
    scope: PreviewRange,
) -> list[dict[str, Any]]:
    return apply_preview_range_to_tools(tool_calls, scope)


def resolve_and_prefetch(
    store: UserStore,
    *,
    route: AgentRoute,
    router_tools: list[dict[str, Any]],
    full_index: dict[str, Any],
    server_today: str | None,
    message: str = "",
    date_anchor: dict[str, Any] | None = None,
    preview_range: PreviewRange | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], PreviewRange | None]:
    """Returns (tool_calls, read_results, errors, preview_range)."""
    tool_calls, tool_errors = validate_tool_plan(router_tools, server_today=server_today)
    if not tool_calls and route != AgentRoute.CHAT:
        tool_calls = default_tools_for_route(route, full_index)

    today = parse_server_date(full_index)
    resolved_scope = preview_range
    if route != AgentRoute.CHAT:
        if resolved_scope is None:
            resolved_scope = resolve_time_scope(message, date_anchor, full_index=full_index)
        else:
            resolved_scope = clamp_preview_range(resolved_scope, today)

        if tool_calls and resolved_scope:
            mentioned = (date_anchor or {}).get("mentioned_weekdays") or {}
            use_day = (
                len(mentioned) == 1
                and not message_has_month_phrase(message)
                and resolved_scope.granularity == "day"
            )
            if use_day:
                day_range = _single_day_range(next(iter(mentioned.values())))
                if day_range:
                    tool_calls = _apply_time_scope(tool_calls, clamp_preview_range(day_range, today))
                else:
                    tool_calls = _apply_time_scope(tool_calls, resolved_scope)
            else:
                tool_calls = _apply_time_scope(tool_calls, resolved_scope)

    if not tool_calls:
        return [], [], tool_errors, resolved_scope

    read_results, exec_errors = run_prefetch(store, tool_calls)
    errors = tool_errors + exec_errors
    return tool_calls, read_results, errors, resolved_scope
