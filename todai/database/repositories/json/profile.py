from __future__ import annotations

from typing import Any

from todai.database.models.paths import UserPaths
from todai.database.utils.json_io import atomic_write_json, read_json


class JsonProfileRepository:
    def __init__(self, paths: UserPaths):
        self._paths = paths

    def read_profile(self) -> dict[str, Any]:
        data = read_json(self._paths.profile)
        if not data:
            raise FileNotFoundError(f"Missing profile: {self._paths.profile}")
        return data

    def write_profile(self, data: dict[str, Any]) -> None:
        atomic_write_json(self._paths.profile, data)

    def profile_exists(self) -> bool:
        return self._paths.profile.exists()
