"""Per-user JSON store — file lock + domain repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from filelock import FileLock

from todai.database.config import DATA_DIR
from todai.database.models.paths import UserPaths
from todai.database.repositories.composite import CompositeUserRepository
from todai.database.repositories.json import (
    JsonCalendarRepository,
    JsonChatRepository,
    JsonProfileRepository,
)


class JsonUserStore:
    def __init__(self, data_dir: Path, user_id: str):
        self.paths = UserPaths(data_dir, user_id)
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.paths.lock_path()), timeout=30)
        profile = JsonProfileRepository(self.paths)
        chat = JsonChatRepository(self.paths)
        calendar = JsonCalendarRepository(self.paths)
        self._repo = CompositeUserRepository(
            user_id=user_id,
            storage_backend="local",
            profile=profile,
            chat=chat,
            calendar=calendar,
            profile_exists=profile.profile_exists(),
            chat_exists=chat.chat_exists(),
        )

    def __enter__(self) -> JsonUserStore:
        self._lock.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._lock.release()

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


UserStore = JsonUserStore
