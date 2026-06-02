"""
Supabase repositories.

Import submodules directly (e.g. ``from todai.database.repositories.supabase.chat import ...``).
Do not eager-import submodules here — chat ↔ message_buckets would circular-import.
"""

__all__ = [
    "SupabaseContext",
    "SupabaseProfileRepository",
    "SupabaseChatRepository",
    "SupabaseCalendarRepository",
    "SupabaseMessageBucketStore",
    "resolve_db_user_id",
    "reset_user_supabase",
]


def __getattr__(name: str):
    """Lazy exports for ``from todai.database.repositories.supabase import X``."""
    if name == "SupabaseContext":
        from todai.database.repositories.supabase.context import SupabaseContext

        return SupabaseContext
    if name == "SupabaseProfileRepository":
        from todai.database.repositories.supabase.profile import SupabaseProfileRepository

        return SupabaseProfileRepository
    if name == "SupabaseChatRepository":
        from todai.database.repositories.supabase.chat import SupabaseChatRepository

        return SupabaseChatRepository
    if name == "SupabaseCalendarRepository":
        from todai.database.repositories.supabase.calendar import SupabaseCalendarRepository

        return SupabaseCalendarRepository
    if name == "SupabaseMessageBucketStore":
        from todai.database.repositories.supabase.message_buckets import SupabaseMessageBucketStore

        return SupabaseMessageBucketStore
    if name == "resolve_db_user_id":
        from todai.database.repositories.supabase.helpers import resolve_db_user_id

        return resolve_db_user_id
    if name == "reset_user_supabase":
        from todai.database.repositories.supabase.reset import reset_user_supabase

        return reset_user_supabase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
