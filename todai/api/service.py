"""
service.py — business entrypoints used by the HTTP API

  process_chat  — orchestrator.orchestrate_turn
  confirm       — stub (changes apply on chat; kept for UI compatibility)
  reject        — stub
  get_debug_state — expose chat FSM + storage index for /api/state
"""

from __future__ import annotations

from typing import Any

from todai.agent.core import orchestrate_turn
from todai.api.middleware.rate_limit import groq_limits, groq_tracker
from todai.database.storage import (
    DATA_DIR,
    ChatResponse,
    UserStore,
    planner_mode,
)


def process_chat(user_id: str, message: str) -> ChatResponse:
    with UserStore(DATA_DIR, user_id) as store:
        return orchestrate_turn(store, user_id=user_id, message=message)


def confirm(user_id: str) -> ChatResponse:
    with UserStore(DATA_DIR, user_id) as store:
        chat = store.read_chat()
        msg = "There's nothing waiting to confirm — changes apply when you ask."
        return ChatResponse(
            assistant_text=msg,
            reply_text=msg,
            state=str(chat.get("state", "idle")),
            schedule_version=int(chat.get("schedule_version", 1)),
            agent_mode=chat.get("last_agent_mode") or "chat",
            agent_state=chat.get("last_agent_mode") or "chat",
            debug={"confirm_stub": True},
        )


def reject(user_id: str) -> ChatResponse:
    with UserStore(DATA_DIR, user_id) as store:
        chat = store.read_chat()
        msg = "There's nothing to cancel right now."
        return ChatResponse(
            assistant_text=msg,
            reply_text=msg,
            state=str(chat.get("state", "idle")),
            schedule_version=int(chat.get("schedule_version", 1)),
            agent_mode=chat.get("last_agent_mode") or "chat",
            agent_state=chat.get("last_agent_mode") or "chat",
            debug={"reject_stub": True},
        )


def get_debug_state(user_id: str, light: bool = True) -> dict[str, Any]:
    with UserStore(DATA_DIR, user_id) as store:
        chat = store.read_chat()
        last_mode = chat.get("last_agent_mode")
        out: dict[str, Any] = {
            "user_id": user_id,
            "state": chat.get("state"),
            "schedule_version": chat.get("schedule_version"),
            "pending_proposal_id": chat.get("pending_proposal_id"),
            "last_turn_id": chat.get("last_turn_id"),
            "last_agent_mode": last_mode,
            "agent_mode": last_mode,
            "planner": planner_mode(),
            "pipeline": "orchestrator",
            "api_usage": groq_tracker.usage_snapshot(user_id),
            "groq_limits": groq_limits(),
        }
        idx = store.planner_storage_index()
        if light:
            out["storage_index"] = {
                "server_date_utc": idx.get("server_date_utc"),
                "calendar_files": [
                    {"month": c.get("month"), "block_count": c.get("block_count")}
                    for c in (idx.get("calendar_files") or [])
                ],
            }
        else:
            out["storage_index"] = idx
        return out
