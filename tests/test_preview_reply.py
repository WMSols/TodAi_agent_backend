from todai.agent.core.preview_reply import build_grounded_preview_reply
from todai.agent.routing.preview_range import PreviewRange


def test_friday_empty_after_delete():
    preview = PreviewRange(
        date_from="2026-05-22",
        date_to="2026-05-22",
        label="Friday, 22 May 2026",
        granularity="day",
        explicit=True,
    )
    read_results = [
        {
            "tool": "get_schedule_range",
            "ok": True,
            "data": {"from": "2026-05-22", "to": "2026-05-22", "blocks": []},
        }
    ]
    reply = build_grounded_preview_reply(
        message="what are my schedules on friday",
        read_results=read_results,
        preview=preview,
    )
    assert reply is not None
    assert "Nothing scheduled" in reply
    assert "Team sync" not in reply


def test_thursday_team_sync_only_on_thursday():
    preview = PreviewRange(
        date_from="2026-05-21",
        date_to="2026-05-21",
        label="Thursday, 21 May 2026",
        granularity="day",
        explicit=True,
    )
    read_results = [
        {
            "tool": "get_schedule_range",
            "ok": True,
            "data": {
                "from": "2026-05-21",
                "to": "2026-05-21",
                "blocks": [
                    {
                        "id": "blk_2",
                        "title": "Team sync",
                        "start": "2026-05-21T14:00:00",
                        "end": "2026-05-21T15:00:00",
                    }
                ],
            },
        }
    ]
    reply = build_grounded_preview_reply(
        message="what is my schedule today",
        read_results=read_results,
        preview=preview,
    )
    assert reply is not None
    assert "Team sync" in reply
