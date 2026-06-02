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
from todai.database import user_store
from todai.database.config import storage_backend_label
from todai.agent.planner.groq_config import planner_mode
from todai.database.config import use_local_storage
from todai.database.models import ChatResponse


def bootstrap_user_profile(
    user_id: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Ensure storage exists for this auth user (first login)."""
    name = (display_name or "").strip() or (email or "").split("@")[0] or "User"
    with user_store(user_id, email=email, display_name=name) as store:
        try:
            profile = store.read_profile()
            if display_name and profile.get("display_name") != name:
                profile["display_name"] = name
                store.write_profile(profile)
        except FileNotFoundError:
            store.write_profile(
                {
                    "user_id": user_id,
                    "display_name": name,
                    "timezone": "UTC",
                    "working_hours": {"start": "09:00", "end": "18:00"},
                    "preferences": {},
                    "goals": [],
                }
            )
    return {
        "ok": True,
        "user_id": user_id,
        "display_name": name,
        "email": email,
        "storage": "local" if use_local_storage() else "supabase",
    }


def process_chat(user_id: str, message: str) -> ChatResponse:
    with user_store(user_id) as store:
        return orchestrate_turn(store, user_id=user_id, message=message)


def confirm(user_id: str) -> ChatResponse:
    with user_store(user_id) as store:
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
    with user_store(user_id) as store:
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
    with user_store(user_id) as store:
        chat = store.read_chat()
        last_mode = chat.get("last_agent_mode")
        out: dict[str, Any] = {
            "user_id": user_id,
            "storage": storage_backend_label(),
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
