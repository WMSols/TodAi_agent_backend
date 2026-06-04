"""Reset user data to seed bundle (Supabase)."""

from __future__ import annotations

from typing import Any


def reset_user_to_seed(user_id: str) -> dict[str, Any]:
    from todai.database.repositories.supabase.reset import reset_user_supabase

    return reset_user_supabase(user_id)
