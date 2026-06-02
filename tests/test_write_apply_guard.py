"""Write apply guard: soft confirm must not block valid operations."""

from todai.agent.core.clarify import reply_blocks_apply, reply_is_clarifying
from todai.agent.core.operation_guard import filter_operations_for_apply


def test_please_confirm_is_clarifying_but_does_not_block_apply():
    reply = "I've added morning walk. Please confirm."
    assert reply_is_clarifying(reply)
    assert not reply_blocks_apply("schedule_write", reply)


def test_real_question_blocks_apply():
    reply = "Which Friday should I use?"
    assert reply_blocks_apply("schedule_write", reply)


def test_filter_keeps_ops_when_only_soft_confirm():
    ops = [
        {
            "op": "add",
            "title": "Morning walk",
            "start": "2026-05-25T09:00:00",
            "end": "2026-05-25T10:00:00",
        }
    ]
    valid, block, _detail = filter_operations_for_apply(
        "schedule_write",
        "Added. Please confirm.",
        ops,
        user_message="9 am to 10 am",
        resolved_scope={"from": "2026-05-25", "to": "2026-05-31"},
    )
    assert block is None
    assert len(valid) == 1
