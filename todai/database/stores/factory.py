"""Open the correct per-user store (local JSON vs Supabase)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

from todai.database.config import DATA_DIR, storage_backend_label, use_local_storage
from todai.database.stores.json_store import JsonUserStore, UserStore

StoreT = Union[JsonUserStore, "SupabaseUserStore"]


@contextmanager
def user_store(
    user_id: str,
    data_dir: Path | None = None,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> Iterator[StoreT]:
    if use_local_storage():
        with JsonUserStore(data_dir or DATA_DIR, user_id) as store:
            yield store
    else:
        from todai.database.stores.supabase_store import SupabaseUserStore

        with SupabaseUserStore(
            user_id, email=email, display_name=display_name
        ) as store:
            yield store


def log_storage_mode(logger) -> None:
    mode = storage_backend_label()
    if mode == "supabase":
        from todai.database.config import supabase_configured

        if not supabase_configured():
            logger.warning(
                "LOCAL=false but SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing — "
                "Supabase calls will fail."
            )
    logger.info("Storage backend: %s", mode)
