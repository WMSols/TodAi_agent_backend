"""Open the per-user Supabase store."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from todai.database.config import storage_backend_label, supabase_configured
from todai.database.stores.supabase_store import SupabaseUserStore

StoreT = SupabaseUserStore
UserStore = SupabaseUserStore


@contextmanager
def user_store(
    user_id: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> Iterator[SupabaseUserStore]:
    with SupabaseUserStore(
        user_id, email=email, display_name=display_name
    ) as store:
        yield store


def log_storage_mode(logger) -> None:
    if not supabase_configured():
        logger.warning(
            "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing — database calls will fail."
        )
    logger.info("Storage backend: %s", storage_backend_label())
