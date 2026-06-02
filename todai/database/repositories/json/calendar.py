from __future__ import annotations

from typing import Any

from todai.database.models.paths import UserPaths
from todai.database.utils.json_io import atomic_write_json, read_json


class JsonCalendarRepository:
    def __init__(self, paths: UserPaths):
        self._paths = paths

    def read_calendar_month(self, year_month: str) -> dict[str, Any]:
        data = read_json(self._paths.calendar_path(year_month))
        if not data:
            return {"month": year_month, "version": 1, "blocks": []}
        return data

    def write_calendar_month(self, year_month: str, data: dict[str, Any]) -> None:
        atomic_write_json(self._paths.calendar_path(year_month), data)

    def find_block_month(self, block_id: str) -> str | None:
        if not block_id:
            return None
        for p in sorted(self._paths.root.glob("calendar_*.json")):
            ym = p.stem.removeprefix("calendar_")
            if any(
                str(b.get("id")) == block_id
                for b in self.read_calendar_month(ym).get("blocks", [])
            ):
                return ym
        return None

    def find_block(self, block_id: str) -> dict[str, Any] | None:
        ym = self.find_block_month(block_id)
        if not ym:
            return None
        for b in self.read_calendar_month(ym).get("blocks") or []:
            if str(b.get("id")) == block_id:
                return dict(b)
        return None

    def calendar_index_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for p in sorted(self._paths.root.glob("calendar_*.json")):
            ym = p.stem.removeprefix("calendar_")
            if len(ym) != 7 or ym[4] != "-":
                continue
            doc = read_json(p) or {}
            blocks = doc.get("blocks") or []
            rows.append(
                {
                    "month": ym,
                    "block_count": len(blocks),
                    "file_version": int(doc.get("version", 1)),
                    "blocks": [
                        {"id": b.get("id"), "title": b.get("title"), "month": ym}
                        for b in blocks
                        if b.get("id")
                    ],
                }
            )
        return rows
