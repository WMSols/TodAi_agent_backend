"""Schedule overlap detection and apply guard."""

from datetime import date

from todai.agent.core.operation_guard import filter_operations_for_apply
from todai.agent.routing.time_scope import (
    message_implies_multi_weekday_scope,
    resolve_preview_range_for_turn,
    scope_from_weekday_candidates,
)
from todai.agent.tools.scheduling import find_conflicts_for_interval, intervals_overlap
from todai.database.stores.json_store import JsonUserStore


def test_intervals_overlap():
    from todai.agent.tools.calendar import parse_iso_dt

    a0 = parse_iso_dt("2026-06-02T10:00:00")
    a1 = parse_iso_dt("2026-06-02T11:00:00")
    b0 = parse_iso_dt("2026-06-02T10:30:00")
    b1 = parse_iso_dt("2026-06-02T11:30:00")
    assert intervals_overlap(a0, a1, b0, b1)
    c0 = parse_iso_dt("2026-06-02T11:00:00")
    c1 = parse_iso_dt("2026-06-02T12:00:00")
    assert not intervals_overlap(a0, a1, c0, c1)


def test_find_conflicts_for_interval():
    from todai.agent.tools.calendar import parse_iso_dt

    blocks = [
        {
            "id": "blk1",
            "title": "cycling",
            "start": "2026-06-02T10:00:00",
            "end": "2026-06-02T11:00:00",
        }
    ]
    start = parse_iso_dt("2026-06-02T10:00:00")
    end = parse_iso_dt("2026-06-02T11:00:00")
    hits = find_conflicts_for_interval(start, end, blocks)
    assert len(hits) == 1
    assert hits[0]["title"] == "cycling"


def test_filter_blocks_overlapping_add(tmp_path):
    data_dir = tmp_path
    with JsonUserStore(data_dir, "default") as store:
        store.write_calendar_month(
            "2026-06",
            {
                "month": "2026-06",
                "version": 1,
                "blocks": [
                    {
                        "id": "blk_cycling",
                        "title": "cycling",
                        "start": "2026-06-02T10:00:00",
                        "end": "2026-06-02T11:00:00",
                        "kind": "focus",
                    }
                ],
            },
        )

    with JsonUserStore(data_dir, "default") as store:
        ops = [
            {
                "op": "add",
                "title": "club",
                "start": "2026-06-02T10:00:00",
                "end": "2026-06-02T11:00:00",
            }
        ]
        valid, block, detail = filter_operations_for_apply(
            "schedule_write",
            "Added club.",
            ops,
            resolved_scope={"from": "2026-06-02", "to": "2026-06-02"},
            store=store,
        )
        assert block == "slot_conflict"
        assert detail is not None
        assert "cycling" in str(detail.get("detail", "")).lower()
        assert len(valid) == 0


def test_filter_allows_non_overlapping_add(tmp_path):
    data_dir = tmp_path
    with JsonUserStore(data_dir, "default") as store:
        store.write_calendar_month(
            "2026-06",
            {
                "month": "2026-06",
                "version": 1,
                "blocks": [
                    {
                        "id": "blk_cycling",
                        "title": "cycling",
                        "start": "2026-06-02T10:00:00",
                        "end": "2026-06-02T11:00:00",
                        "kind": "focus",
                    }
                ],
            },
        )

    with JsonUserStore(data_dir, "default") as store:
        ops = [
            {
                "op": "add",
                "title": "club",
                "start": "2026-06-02T11:00:00",
                "end": "2026-06-02T12:00:00",
            }
        ]
        valid, block, _detail = filter_operations_for_apply(
            "schedule_write",
            "Added club.",
            ops,
            resolved_scope={"from": "2026-06-02", "to": "2026-06-02"},
            store=store,
        )
        assert block is None
        assert len(valid) == 1


def test_multi_weekday_scope_from_candidates():
    today = date(2026, 6, 1)
    anchor = {
        "today": {"iso": today.isoformat()},
        "weekday_candidates": {
            "wednesday": [
                {"iso": "2026-06-03", "label": "Wednesday, 03 June 2026"},
                {"iso": "2026-06-10", "label": "Wednesday, 10 June 2026"},
            ]
        },
    }
    msg = "reading on this wednesday and next wednesday, 3pm to 4pm"
    assert message_implies_multi_weekday_scope(msg, anchor)
    scope = scope_from_weekday_candidates(anchor, today)
    assert scope is not None
    assert scope.date_from == "2026-06-03"
    assert scope.date_to == "2026-06-10"

    resolved = resolve_preview_range_for_turn(
        time_scope="single_day",
        message=msg,
        date_anchor=anchor,
        full_index={"server_date_utc": "2026-06-01"},
        route="schedule_write",
    )
    assert resolved.date_from == "2026-06-03"
    assert resolved.date_to == "2026-06-10"
