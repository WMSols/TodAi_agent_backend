"""Weekday resolution — this/next phrasing and agent window."""

from datetime import date

from todai.agent.routing.date_anchor import resolve_weekday_context
from todai.agent.routing.time_scope import (
    message_implies_multi_weekday_scope,
    resolve_preview_range_for_turn,
    scope_from_weekday_candidates,
)


def test_this_and_next_friday_yields_two_candidates_not_single_mentioned():
    today = date(2026, 6, 1)  # Sunday
    wctx = resolve_weekday_context(
        "okay add reading on this friday and on next friday, timing 2pm to 3pm",
        today,
    )
    assert "friday" not in (wctx.get("mentioned_weekdays") or {})
    cand = (wctx.get("weekday_candidates") or {}).get("friday") or []
    isos = sorted(o["iso"] for o in cand)
    assert isos == ["2026-06-05", "2026-06-12"]


def test_plain_friday_is_ambiguous_candidates():
    today = date(2026, 6, 1)
    wctx = resolve_weekday_context("add lunch on friday 2pm to 3pm", today)
    assert "friday" not in (wctx.get("mentioned_weekdays") or {})
    assert len((wctx.get("weekday_candidates") or {}).get("friday") or []) == 2


def test_multi_weekday_scope_for_this_and_next_friday():
    today = date(2026, 6, 1)
    anchor = {
        "today": {"iso": today.isoformat()},
        "weekday_candidates": {
            "friday": [
                {"iso": "2026-06-05", "label": "Friday, 05 June 2026"},
                {"iso": "2026-06-12", "label": "Friday, 12 June 2026"},
            ]
        },
    }
    msg = "reading on this friday and next friday, 2pm to 3pm"
    assert message_implies_multi_weekday_scope(msg, anchor)
    scope = scope_from_weekday_candidates(anchor, today)
    assert scope is not None
    assert scope.date_from == "2026-06-05"
    assert scope.date_to == "2026-06-12"

    resolved = resolve_preview_range_for_turn(
        time_scope="single_day",
        message=msg,
        date_anchor=anchor,
        full_index={"server_date_utc": "2026-06-01"},
        route="schedule_write",
    )
    assert resolved.date_from == "2026-06-05"
    assert resolved.date_to == "2026-06-12"
