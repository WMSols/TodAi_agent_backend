"""In-memory document shapes (JSON files and agent-facing dicts)."""

from __future__ import annotations

from typing import Any, TypedDict


class CalendarBlock(TypedDict, total=False):
    id: str
    title: str
    start: str
    end: str
    kind: str


class CalendarMonthDoc(TypedDict, total=False):
    month: str
    version: int
    blocks: list[dict[str, Any]]


class ChatDocument(TypedDict, total=False):
    conversation_id: str
    state: str
    schedule_version: int
    pending_proposal_id: str | None
    pending_proposal: Any
    last_turn_id: str | None
    last_agent_mode: str | None
    messages: list[dict[str, Any]]


def empty_chat_document(conversation_id: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "state": "idle",
        "schedule_version": 1,
        "pending_proposal_id": None,
        "pending_proposal": None,
        "last_turn_id": None,
        "messages": [],
    }
