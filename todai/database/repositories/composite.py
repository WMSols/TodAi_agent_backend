"""Compose profile, chat, and calendar repositories into one user store."""

from __future__ import annotations

from typing import Any

from todai.database.repositories.protocols import (
    CalendarRepository,
    ChatRepository,
    ProfileRepository,
)
from todai.database.utils.dates import resolve_user_timezone, server_date_fields


class CompositeUserRepository:
    """Agent-facing store API built from domain repositories."""

    def __init__(
        self,
        *,
        user_id: str,
        storage_backend: str,
        profile: ProfileRepository,
        chat: ChatRepository,
        calendar: CalendarRepository,
        profile_exists: bool | None = None,
        chat_exists: bool | None = None,
    ):
        self._user_id = user_id
        self._backend = storage_backend
        self._profile = profile
        self._chat = chat
        self._calendar = calendar
        self._profile_exists = profile_exists
        self._chat_exists = chat_exists

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def storage_backend(self) -> str:
        return self._backend

    def read_profile(self) -> dict[str, Any]:
        return self._profile.read_profile()

    def write_profile(self, data: dict[str, Any]) -> None:
        self._profile.write_profile(data)

    def read_chat(self) -> dict[str, Any]:
        return self._chat.read_chat()

    def write_chat(self, data: dict[str, Any]) -> None:
        self._chat.write_chat(data)

    def read_calendar_month(self, year_month: str) -> dict[str, Any]:
        return self._calendar.read_calendar_month(year_month)

    def write_calendar_month(self, year_month: str, data: dict[str, Any]) -> None:
        self._calendar.write_calendar_month(year_month, data)

    def find_block_month(self, block_id: str) -> str | None:
        return self._calendar.find_block_month(block_id)

    def find_block(self, block_id: str) -> dict[str, Any] | None:
        return self._calendar.find_block(block_id)

    def mark_profile_ready(self) -> None:
        self._profile_exists = True

    def profile_exists(self) -> bool:
        if self._profile_exists is not None:
            return self._profile_exists
        if hasattr(self._profile, "profile_exists"):
            return bool(self._profile.profile_exists())  # type: ignore[attr-defined]
        try:
            self.read_profile()
            return True
        except FileNotFoundError:
            return False

    def chat_exists(self) -> bool:
        if self._chat_exists is not None:
            return self._chat_exists
        if hasattr(self._chat, "chat_exists"):
            return bool(self._chat.chat_exists())  # type: ignore[attr-defined]
        return True

    def planner_storage_index(self) -> dict[str, Any]:
        profile_full: dict[str, Any] | None = None
        profile_tip: dict[str, Any] = {}
        try:
            profile_full = self.read_profile()
            profile_tip = {
                "display_name": profile_full.get("display_name"),
                "timezone": profile_full.get("timezone"),
            }
        except FileNotFoundError:
            pass

        calendar_rows = self._calendar.calendar_index_rows()
        flat_ids = [
            b["id"] for row in calendar_rows for b in row.get("blocks") or [] if b.get("id")
        ]
        server_date, server_dt = server_date_fields(profile_full)
        return {
            "user_id": self._user_id,
            "storage_backend": self._backend,
            "server_date_utc": server_date,
            "server_datetime_utc": server_dt,
            "server_timezone": resolve_user_timezone(profile_full),
            "profile_path_exists": self.profile_exists(),
            "profile": profile_tip,
            "chat_path_exists": self.chat_exists(),
            "calendar_files": calendar_rows,
            "years_with_calendar_json": sorted({row["month"][:4] for row in calendar_rows}),
            "known_block_ids": flat_ids,
        }
