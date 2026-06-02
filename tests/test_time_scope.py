"""Router time_scope keywords and message phrase expansion."""

from datetime import date

from todai.agent.routing.date_anchor import build_date_anchor, resolve_weekday_context
from todai.agent.routing.preview_range import resolve_time_scope
from todai.agent.routing.time_scope import (
    infer_time_scope_from_message,
    normalize_time_scope,
    resolve_preview_range_for_turn,
    strip_router_tool_dates,
)
from todai.agent.tools.calendar import validate_tool_plan


def test_normalize_next_all_week_alias():
    assert normalize_time_scope("next_all_week") == "next_week"
    assert normalize_time_scope("all_next_week") == "next_week"


def test_message_next_all_week_is_next_calendar_week():
    today = date(2026, 5, 21)  # Thursday
    scope = resolve_time_scope("what is on next all week", {"today": {"iso": today.isoformat()}}, full_index={"server_date_utc": "2026-05-21"})
    assert scope.date_from == "2026-05-25"  # Monday
    assert scope.date_to == "2026-05-31"
    assert scope.granularity == "week"


def test_resolve_from_router_keyword_next_week():
    scope = resolve_preview_range_for_turn(
        time_scope="next_week",
        message="show me",
        date_anchor={"today": {"iso": "2026-05-21"}},
        full_index={"server_date_utc": "2026-05-21"},
    )
    assert scope.date_from == "2026-05-25"
    assert scope.date_to == "2026-05-31"


def test_infer_time_scope_phrase():
    assert infer_time_scope_from_message("preview next all week") == "next_week"


def test_validate_tool_plan_allows_empty_range_args():
    calls, errs = validate_tool_plan([{"tool": "get_schedule_range", "arguments": {}}])
    assert not errs
    assert calls == [{"tool": "get_schedule_range", "arguments": {}}]


def test_strip_router_tool_dates():
    raw = [{"tool": "get_schedule_range", "arguments": {"from": "2026-01-01", "to": "2026-01-07"}}]
    assert strip_router_tool_dates(raw) == [{"tool": "get_schedule_range", "arguments": {}}]


def test_next_saturday_on_friday_is_second_saturday():
    today = date(2026, 5, 22)  # Friday
    wctx = resolve_weekday_context("remove my schedule of next saturday", today)
    assert wctx.get("mentioned_weekdays", {}).get("saturday") == "2026-05-30"


def test_router_next_week_refined_to_next_saturday_day():
    today = date(2026, 5, 22)
    anchor = build_date_anchor({"server_date_utc": "2026-05-22"}, message="remove my schedule of next saturday")
    scope = resolve_preview_range_for_turn(
        time_scope="next_week",
        message="remove my schedule of next saturday",
        date_anchor=anchor,
        full_index={"server_date_utc": "2026-05-22"},
        route="schedule_delete",
    )
    assert scope.date_from == scope.date_to == "2026-05-30"
    assert scope.granularity == "day"


def test_infer_next_saturday_is_single_day():
    assert infer_time_scope_from_message("remove schedule of next saturday") == "single_day"
