"""Filesystem paths for per-user JSON storage."""

from __future__ import annotations

from pathlib import Path


class UserPaths:
    def __init__(self, data_dir: Path, user_id: str):
        self.user_id = user_id
        self.root = data_dir / "users" / user_id
        self.profile = self.root / "profile.json"
        self.chat = self.root / "chat.json"

    def calendar_path(self, year_month: str) -> Path:
        return self.root / f"calendar_{year_month}.json"

    def lock_path(self) -> Path:
        return self.root / ".user.lock"
