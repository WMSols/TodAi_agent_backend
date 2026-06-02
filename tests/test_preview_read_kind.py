"""Preview read kind + days-without-schedule tool."""

from datetime import date

from todai.agent.core.prefetch_tools import augment_preview_tool_calls
from todai.agent.routing.preview_range import PreviewRange
from todai.agent.routing.preview_read_kind import PreviewReadKind, classify_preview_read
from todai.agent.tools.calendar import CalendarService
from todai.database.storage import UserStore


def test_classify_free_days_vs_free_time():
    assert classify_preview_read("what are my free days") == PreviewReadKind.FREE_DAYS
    assert classify_preview_read("days without schedule") == PreviewReadKind.FREE_DAYS
    assert classify_preview_read("when am i free tomorrow") == PreviewReadKind.FREE_TIME
    assert classify_preview_read("what is my schedule") == PreviewReadKind.SCHEDULE


def test_augment_tools_free_days():
    scope = PreviewRange(
        date_from="2026-05-21",
        date_to="2026-06-03",
        label="window",
        granularity="week",
        explicit=True,
    )
    calls = augment_preview_tool_calls(
        [{"tool": "get_free_time", "arguments": {"from": "2026-05-21", "to": "2026-05-31"}}],
        message="what are my free days",
        scope=scope,
    )
    tools = {c["tool"] for c in calls}
    assert "get_days_without_schedule" in tools
    assert "get_schedule_range" in tools
    assert "get_free_time" not in tools


def test_augment_tools_free_time():
    scope = PreviewRange(
        date_from="2026-05-21",
        date_to="2026-06-03",
        label="window",
        granularity="week",
        explicit=True,
    )
    calls = augment_preview_tool_calls(
        [{"tool": "get_days_without_schedule", "arguments": {"from": "2026-05-21", "to": "2026-06-03"}}],
        message="when am i free",
        scope=scope,
    )
    tools = {c["tool"] for c in calls}
    assert "get_free_time" in tools
    assert "get_days_without_schedule" not in tools


def test_days_without_schedule_empty_day(tmp_path):
    data_dir = tmp_path / "data"
    user = data_dir / "users" / "default"
    user.mkdir(parents=True)
    (user / "calendar_2026-05.json").write_text(
        '{"month":"2026-05","version":1,"blocks":[{"id":"b1","title":"Meet","start":"2026-05-21T10:00:00","end":"2026-05-21T11:00:00"}]}',
        encoding="utf-8",
    )
    (user / "profile.json").write_text('{"goals":[]}', encoding="utf-8")
    with UserStore(data_dir, "default") as store:
        svc = CalendarService(store)
        out = svc.days_without_schedule(date(2026, 5, 21), date(2026, 5, 23))
    dates = [d["date"] for d in out["days_without_schedule"]]
    assert "2026-05-21" not in dates
    assert "2026-05-22" in dates
    assert "2026-05-23" in dates
