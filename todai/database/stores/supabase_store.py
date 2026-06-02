"""Supabase store — composes domain repositories."""

from __future__ import annotations

from typing import Any

from todai.database.models.paths import UserPaths
from todai.database.repositories.composite import CompositeUserRepository
from todai.database.repositories.supabase.calendar import SupabaseCalendarRepository
from todai.database.repositories.supabase.chat import SupabaseChatRepository
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.helpers import get_supabase_client, resolve_db_user_id
from todai.database.repositories.supabase.profile import SupabaseProfileRepository
from todai.database.repositories.supabase.reset import reset_user_supabase

__all__ = ["SupabaseUserStore", "reset_user_supabase", "resolve_db_user_id"]


class SupabaseUserStore:
    def __init__(
        self,
        user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ):
        self.api_user_id = user_id
        self._bootstrap_email = email
        self._bootstrap_display_name = display_name
        self.db_user_id = resolve_db_user_id(user_id)
        self._ctx = SupabaseContext(
            client=get_supabase_client(),
            api_user_id=user_id,
            db_user_id=self.db_user_id,
        )
        self.paths = UserPaths.__new__(UserPaths)
        self.paths.user_id = user_id
        self.paths.root = None  # type: ignore[assignment]
        self._profile = SupabaseProfileRepository(self._ctx)
        self._calendar = SupabaseCalendarRepository(self._ctx, self._profile)
        self._chat = SupabaseChatRepository(self._ctx)
        self._repo = CompositeUserRepository(
            user_id=user_id,
            storage_backend="supabase",
            profile=self._profile,
            chat=self._chat,
            calendar=self._calendar,
            profile_exists=False,
            chat_exists=True,
        )

    def __enter__(self) -> SupabaseUserStore:
        self._profile.ensure_user(
            self._calendar,
            email=self._bootstrap_email,
            display_name=self._bootstrap_display_name,
        )
        self._repo.mark_profile_ready()
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read_profile(self) -> dict[str, Any]:
        return self._repo.read_profile()

    def write_profile(self, data: dict[str, Any]) -> None:
        self._repo.write_profile(data)

    def read_chat(self) -> dict[str, Any]:
        return self._repo.read_chat()

    def write_chat(self, data: dict[str, Any]) -> None:
        self._repo.write_chat(data)

    def read_calendar_month(self, year_month: str) -> dict[str, Any]:
        return self._repo.read_calendar_month(year_month)

    def write_calendar_month(self, year_month: str, data: dict[str, Any]) -> None:
        self._repo.write_calendar_month(year_month, data)

    def find_block_month(self, block_id: str) -> str | None:
        return self._repo.find_block_month(block_id)

    def find_block(self, block_id: str) -> dict[str, Any] | None:
        return self._repo.find_block(block_id)

    def planner_storage_index(self) -> dict[str, Any]:
        return self._repo.planner_storage_index()
