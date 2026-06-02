"""Local JSON message buckets (mirrors Supabase bucket semantics)."""

from __future__ import annotations

import uuid
from typing import Any

from todai.database.buckets import BucketLimits, MessageChannel, normalize_message_row


def ensure_bucket_structure(
    data: dict[str, Any],
    *,
    channel: MessageChannel = "chat",
) -> dict[str, Any]:
    buckets = data.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        bid = str(uuid.uuid4())
        data["buckets"] = [{"id": bid, "channel": channel, "messages": data.get("messages") or []}]
        data["active_bucket_id"] = bid
    if not data.get("active_bucket_id"):
        data["active_bucket_id"] = data["buckets"][0]["id"]
    return data


def active_bucket_messages(data: dict[str, Any]) -> list[dict[str, Any]]:
    data = ensure_bucket_structure(data)
    bid = data.get("active_bucket_id")
    for b in data.get("buckets") or []:
        if isinstance(b, dict) and b.get("id") == bid:
            return list(b.get("messages") or [])
    return list(data.get("messages") or [])


def sync_flat_messages(data: dict[str, Any]) -> None:
    """Keep top-level messages[] in sync with active bucket (UI / orchestrator)."""
    msgs = active_bucket_messages(data)
    data["messages"] = msgs


def replace_bucket_messages(
    data: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    limits: BucketLimits,
    channel: MessageChannel = "chat",
) -> None:
    data = ensure_bucket_structure(data, channel=channel)
    trimmed = limits.trimmed(
        [r for m in messages if (r := normalize_message_row(m)) is not None]
    )
    bid = data["active_bucket_id"]
    for b in data.get("buckets") or []:
        if isinstance(b, dict) and b.get("id") == bid:
            b["messages"] = trimmed
            break
    data["messages"] = trimmed
