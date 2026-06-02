"""Preview scope: ambiguous weekday → nearest day; timezone today."""

from datetime import date

from todai.agent.routing.weekday_pick import pick_nearest_weekday_option
from todai.agent.routing.preview_range import resolve_preview_range
from todai.database.storage import server_date_fields


def test_pick_nearest_friday():
    opts = [
        {"iso": "2026-05-22", "label": "Friday, 22 May 2026"},
        {"iso": "2026-05-29", "label": "Friday, 29 May 2026"},
    ]
    assert pick_nearest_weekday_option(opts, date(2026, 5, 21)) == "2026-05-22"


def test_schedules_on_friday_single_day_scope():
    today = date(2026, 5, 21)
    anchor = {
        "today": {"iso": "2026-05-21", "weekday": "Thursday"},
        "weekday_candidates": {
            "friday": [
                {"iso": "2026-05-22", "label": "Friday, 22 May 2026"},
                {"iso": "2026-05-29", "label": "Friday, 29 May 2026"},
            ]
        },
    }
    scope = resolve_preview_range("what are my schedules on friday", anchor, full_index={"server_date_utc": "2026-05-21"})
    assert scope.date_from == scope.date_to == "2026-05-22"
    assert scope.granularity == "day"


def test_server_date_fields_returns_iso():
    d, dt = server_date_fields({"timezone": "UTC"})
    assert len(d) == 10
    assert "T" in dt or len(dt) >= 10
