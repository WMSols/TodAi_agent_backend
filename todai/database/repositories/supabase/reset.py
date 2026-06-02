from __future__ import annotations

from typing import Any

from todai.database.models.entities import empty_chat_document
from todai.database.repositories.supabase.calendar import SupabaseCalendarRepository
from todai.database.repositories.supabase.chat import SupabaseChatRepository
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.helpers import get_supabase_client, resolve_db_user_id
from todai.database.repositories.supabase.profile import SupabaseProfileRepository


def reset_user_supabase(user_id: str) -> dict[str, Any]:
    db_id = resolve_db_user_id(user_id)
    client = get_supabase_client()
    for table, col in (
        ("goal_tasks", "user_id"),
        ("goal_week_plans", "user_id"),
        ("goals", "user_id"),
        ("agent_turns", "user_id"),
        ("agent_memories", "user_id"),
        ("messages", "user_id"),
        ("message_buckets", "user_id"),
        ("conversation_context", None),
        ("conversations", "user_id"),
        ("calendar_events", "user_id"),
    ):
        if table == "conversation_context":
            convs = client.table("conversations").select("id").eq("user_id", db_id).execute()
            for c in convs.data or []:
                client.table("conversation_context").delete().eq(
                    "conversation_id", c["id"]
                ).execute()
            continue
        client.table(table).delete().eq(col, db_id).execute()

    ctx = SupabaseContext(client=client, api_user_id=user_id, db_user_id=db_id)
    profile = SupabaseProfileRepository(ctx)
    calendar = SupabaseCalendarRepository(ctx, profile)
    chat = SupabaseChatRepository(ctx)
    calendar.seed_from_files()
    chat.write_chat(empty_chat_document(user_id))
    return {
        "ok": True,
        "user_id": user_id,
        "message": "Calendar and chat reset in Supabase.",
        "storage_backend": "supabase",
    }
