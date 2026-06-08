"""Server today for goal planner prompts (same clock as calendar agent)."""

from __future__ import annotations

from typing import Any

from todai.database import user_store
from todai.database.utils.dates import build_today_info


def get_server_today_for_user(user_id: str) -> dict[str, str]:
    """Load planner storage index when possible; else env/profile timezone."""
    try:
        store = user_store.UserStore(user_id)
        idx = store.planner_storage_index()
        return build_today_info(storage_index=idx)
    except Exception:
        return build_today_info()


def today_payload_for_llm(today: dict[str, str] | None = None) -> dict[str, Any]:
    t = today or build_today_info()
    return {"server_today": t}
