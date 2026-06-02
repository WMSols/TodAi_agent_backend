"""Re-export — implementation lives in supabase.message_buckets (avoids circular imports)."""

from todai.database.repositories.supabase.message_buckets import SupabaseMessageBucketStore

__all__ = ["SupabaseMessageBucketStore"]
