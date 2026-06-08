"""
prefetch.py — validate router tool plan, align preview reads, execute tools before intents.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from todai.agent.tools.calendar import run_prefetch, validate_tool_plan
from todai.agent.planner.llm import AgentRoute, default_tools_for_route
from todai.agent.routing.preview_range import (
    PreviewRange,
    PreviewReadKind,
    apply_preview_range_to_tools,
    build_discrete_preview_targets,
    classify_preview_read,
    clamp_preview_range,
    message_has_month_phrase,
    message_implies_multi_weekday_scope,
    message_implies_single_day,
    normalize_time_scope,
    pick_nearest_weekday_option,
    preview_range_from_discrete_targets,
    refine_scope_for_message,
    resolve_preview_range_for_turn,
    scope_from_mentioned_weekdays,
    scope_from_weekday_candidates,
    strip_router_tool_dates,
)
from todai.agent.routing.date_anchor import single_day_iso_from_anchor
from todai.database.storage import UserStore, parse_server_date

_SPECIALTY_TOOLS = frozenset({"get_free_time", "get_days_without_schedule"})
_DISCRETE_SCOPE_ROUTES = frozenset(
    {
        AgentRoute.SCHEDULE_PREVIEW,
        AgentRoute.SCHEDULE_DELETE,
        AgentRoute.SCHEDULE_WRITE,
    }
)


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
    time_scope: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], PreviewRange | None]:
    """Returns (tool_calls, read_results, errors, preview_range)."""
    time_scope = normalize_time_scope(time_scope)
    router_tools = strip_router_tool_dates(router_tools)
    tool_calls, tool_errors = validate_tool_plan(router_tools, server_today=server_today)
    if not tool_calls and route != AgentRoute.CHAT:
        tool_calls = default_tools_for_route(route, full_index)

    today = parse_server_date(full_index)
    resolved_scope = preview_range
    if route != AgentRoute.CHAT:
        if resolved_scope is None:
            resolved_scope = resolve_preview_range_for_turn(
                time_scope=time_scope,
                message=message,
                date_anchor=date_anchor,
                full_index=full_index,
                route=route.value,
            )
        else:
            resolved_scope = clamp_preview_range(resolved_scope, today)
        resolved_scope = refine_scope_for_message(
            resolved_scope,
            message=message,
            date_anchor=date_anchor,
            today=today,
        )

        if route in _DISCRETE_SCOPE_ROUTES:
            discrete_targets = build_discrete_preview_targets(message, date_anchor, today)
            discrete_scope = preview_range_from_discrete_targets(discrete_targets, today)
            if discrete_scope:
                resolved_scope = discrete_scope

        candidates = (date_anchor or {}).get("weekday_candidates") or {}
        if route not in _DISCRETE_SCOPE_ROUTES and message_implies_multi_weekday_scope(
            message, date_anchor
        ):
            multi = scope_from_mentioned_weekdays(date_anchor, today)
            if multi:
                resolved_scope = multi
            else:
                multi = scope_from_weekday_candidates(date_anchor, today)
                if multi:
                    resolved_scope = multi
        elif len(candidates) == 1:
            iso = pick_nearest_weekday_option(next(iter(candidates.values())), today)
            if iso:
                day_range = _single_day_range(iso)
                if day_range:
                    resolved_scope = clamp_preview_range(day_range, today)

        if tool_calls and resolved_scope:
            if (
                route not in _DISCRETE_SCOPE_ROUTES
                or resolved_scope.scope_mode != "discrete_days"
            ) and message_implies_single_day(message, date_anchor) and not message_has_month_phrase(
                message
            ):
                iso = single_day_iso_from_anchor(date_anchor)
                if iso:
                    day_range = _single_day_range(iso)
                    if day_range:
                        resolved_scope = clamp_preview_range(day_range, today)
            tool_calls = _apply_time_scope(tool_calls, resolved_scope)

        if route == AgentRoute.SCHEDULE_PREVIEW and resolved_scope:
            tool_calls = augment_preview_tool_calls(
                tool_calls, message=message, scope=resolved_scope
            )

    if not tool_calls:
        return [], [], tool_errors, resolved_scope

    read_results, exec_errors = run_prefetch(store, tool_calls)
    errors = tool_errors + exec_errors
    return tool_calls, read_results, errors, resolved_scope
