"""Message bucket limits (env) and shared message shapes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

MessageChannel = Literal["chat", "goal_plan"]

CHANNEL_CHAT: MessageChannel = "chat"
CHANNEL_GOAL_PLAN: MessageChannel = "goal_plan"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class BucketLimits:
    store: int
    pull: int

    def trimmed(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(messages) <= self.store:
            return messages
        return messages[-self.store :]


def chat_bucket_limits() -> BucketLimits:
    store = _env_int("TODAI_CHAT_BUCKET_STORE", 20)
    pull = _env_int("TODAI_CHAT_BUCKET_PULL", 5)
    return BucketLimits(store=store, pull=min(pull, store))


def goal_bucket_limits() -> BucketLimits:
    store = _env_int("TODAI_GOAL_BUCKET_STORE", 30)
    pull = _env_int("TODAI_GOAL_BUCKET_PULL", 10)
    return BucketLimits(store=store, pull=min(pull, store))


def chat_router_pull_limit() -> int:
    return _env_int("TODAI_CHAT_ROUTER_PULL", 3)


def normalize_message_row(m: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(m, dict):
        return None
    role = m.get("role")
    if role not in ("user", "assistant"):
        return None
    content = str(m.get("content") or "")
    row: dict[str, Any] = {"role": role, "content": content}
    meta = m.get("meta")
    if isinstance(meta, dict) and meta:
        row["meta"] = meta
    return row


def messages_for_llm(
    messages: list[dict[str, Any]],
    *,
    pull: int,
    max_chars: int = 3500,
) -> list[dict[str, str]]:
    """Last N UX messages for Groq (role + content only)."""
    rows: list[dict[str, str]] = []
    for m in messages[-pull:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(truncated)"
        rows.append({"role": str(m["role"]), "content": text})
    return rows
