"""Build refreshed week display after calendar writes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from todai.agent.tools.calendar import execute_read_tools
from todai.agent.core.display import build_schedule_display
from todai.agent.routing.preview_range import agent_window_bounds
from todai.database.storage import UserStore, parse_server_date


def build_week_schedule_display(store: UserStore, full_index: dict[str, Any]) -> dict[str, Any] | None:
    today = parse_server_date(full_index)
    _, period_end = agent_window_bounds(today)
    period_to = period_end.isoformat()
    read_results, _ = execute_read_tools(
        store,
        [{"tool": "get_schedule_range", "arguments": {"from": today.isoformat(), "to": period_to}}],
    )
    return build_schedule_display(
        read_results,
        period_from=today.isoformat(),
        period_to=period_to,
        fill_empty_days=True,
    )
