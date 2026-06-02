from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client


@dataclass
class SupabaseContext:
    client: Client
    api_user_id: str
    db_user_id: str
    tz: str | None = None
    conversation_id: str | None = None
