"""Message bucket limits and JSON bucket storage."""

from todai.database.buckets import chat_bucket_limits, goal_bucket_limits, messages_for_llm
from todai.database.repositories.json.buckets import (
    ensure_bucket_structure,
    replace_bucket_messages,
)


def test_chat_bucket_trim():
    limits = chat_bucket_limits()
    assert limits.pull <= limits.store
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(25)]
    trimmed = limits.trimmed(msgs)
    assert len(trimmed) == limits.store


def test_messages_for_llm_pull():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 5
    out = messages_for_llm(msgs, pull=3)
    assert len(out) == 3
    assert out[-1]["content"] == "b"


def test_json_bucket_replace():
    data = ensure_bucket_structure({"conversation_id": "u1", "messages": []})
    limits = chat_bucket_limits()
    long = [{"role": "user", "content": str(i)} for i in range(30)]
    replace_bucket_messages(data, long, limits=limits)
    assert len(data["messages"]) == limits.store
    assert len(data["buckets"][0]["messages"]) == limits.store


def test_goal_limits_defaults():
    g = goal_bucket_limits()
    assert g.store >= g.pull
