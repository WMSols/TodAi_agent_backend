from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from typing import Any, TYPE_CHECKING
from todai.database.config import seed_dir
from todai.database.utils.tz import get_timezone
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.helpers import (
    _parse_uuid,
    local_naive_to_utc,
    month_bounds,
    parse_ts,
    utc_to_local_naive_str,
)
from todai.database.utils.json_io import read_json

if TYPE_CHECKING:
    from todai.database.repositories.supabase.profile import SupabaseProfileRepository


class SupabaseCalendarRepository:
    def __init__(self, ctx: SupabaseContext, profile: SupabaseProfileRepository):
        self._ctx = ctx
        self._profile = profile

    def seed_from_files(self) -> None:
        sd = seed_dir()
        if not sd.is_dir():
            return
        for src in sorted(sd.glob("calendar_*.json")):
            ym = src.stem.removeprefix("calendar_")
            doc = read_json(src) or {}
            blocks = doc.get("blocks") or []
            if blocks:
                self.write_calendar_month(
                    ym,
                    {"month": ym, "version": int(doc.get("version", 1)), "blocks": blocks},
                )

    def _event_to_block(self, row: dict[str, Any]) -> dict[str, Any]:
        tz = self._profile.tz_name()
        return {
            "id": str(row["id"]),
            "title": row.get("title") or "Block",
            "start": utc_to_local_naive_str(parse_ts(row["start_at"]), tz),
            "end": utc_to_local_naive_str(parse_ts(row["end_at"]), tz),
            "kind": row.get("kind") or "focus",
        }

    def read_calendar_month(self, year_month: str) -> dict[str, Any]:
        tz = self._profile.tz_name()
        m_start, m_end = month_bounds(year_month)
        z = get_timezone(tz)
        range_start = datetime.combine(m_start, time.min, tzinfo=z).astimezone(timezone.utc)
        range_end = datetime.combine(m_end, time.max.replace(microsecond=0), tzinfo=z).astimezone(
            timezone.utc
        )
        rows = (
            self._ctx.client.table("calendar_events")
            .select("id, title, start_at, end_at, kind, status, deleted_at")
            .eq("user_id", self._ctx.db_user_id)
            .is_("deleted_at", "null")
            .eq("status", "confirmed")
            .gte("start_at", range_start.isoformat())
            .lte("start_at", range_end.isoformat())
            .order("start_at")
            .execute()
        )
        return {
            "month": year_month,
            "version": 1,
            "blocks": [self._event_to_block(r) for r in (rows.data or [])],
        }

    def write_calendar_month(self, year_month: str, data: dict[str, Any]) -> None:
        tz = self._profile.tz_name()
        blocks = data.get("blocks") or []
        m_start, m_end = month_bounds(year_month)
        z = get_timezone(tz)
        range_start = datetime.combine(m_start, time.min, tzinfo=z).astimezone(timezone.utc)
        range_end = datetime.combine(m_end, time.max.replace(microsecond=0), tzinfo=z).astimezone(
            timezone.utc
        )
        existing = (
            self._ctx.client.table("calendar_events")
            .select("id")
            .eq("user_id", self._ctx.db_user_id)
            .is_("deleted_at", "null")
            .gte("start_at", range_start.isoformat())
            .lte("start_at", range_end.isoformat())
            .execute()
        )
        existing_ids = {str(r["id"]) for r in (existing.data or [])}
        keep_ids: set[str] = set()
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            bid = _parse_uuid(str(blk.get("id") or "")) or str(uuid.uuid4())
            keep_ids.add(bid)
            self._ctx.client.table("calendar_events").upsert(
                {
                    "id": bid,
                    "user_id": self._ctx.db_user_id,
                    "title": blk.get("title") or "Block",
                    "start_at": local_naive_to_utc(str(blk["start"]), tz).isoformat(),
                    "end_at": local_naive_to_utc(str(blk["end"]), tz).isoformat(),
                    "kind": blk.get("kind") or "focus",
                    "source": "agent",
                    "status": "confirmed",
                    "deleted_at": None,
                }
            ).execute()
        for eid in existing_ids - keep_ids:
            self._ctx.client.table("calendar_events").update(
                {
                    "status": "cancelled",
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", eid).execute()

    def find_block_month(self, block_id: str) -> str | None:
        bid = _parse_uuid(block_id)
        if not bid:
            return None
        row = (
            self._ctx.client.table("calendar_events")
            .select("start_at")
            .eq("id", bid)
            .eq("user_id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        if not row.data:
            return None
        start = parse_ts(row.data[0]["start_at"])
        local = start.astimezone(get_timezone(self._profile.tz_name()))
        return f"{local.year}-{local.month:02d}"

    def find_block(self, block_id: str) -> dict[str, Any] | None:
        bid = _parse_uuid(block_id)
        if not bid:
            return None
        row = (
            self._ctx.client.table("calendar_events")
            .select("id, title, start_at, end_at, kind")
            .eq("id", bid)
            .eq("user_id", self._ctx.db_user_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        if not row.data:
            return None
        return self._event_to_block(row.data[0])

    def calendar_index_rows(self) -> list[dict[str, Any]]:
        rows = (
            self._ctx.client.table("calendar_events")
            .select("id, title, start_at")
            .eq("user_id", self._ctx.db_user_id)
            .is_("deleted_at", "null")
            .eq("status", "confirmed")
            .order("start_at")
            .execute()
        )
        by_month: dict[str, list[dict[str, Any]]] = {}
        tz = self._profile.tz_name()
        for r in rows.data or []:
            start = parse_ts(r["start_at"])
            local = start.astimezone(get_timezone(tz))
            ym = f"{local.year}-{local.month:02d}"
            by_month.setdefault(ym, []).append(
                {"id": str(r["id"]), "title": r.get("title"), "month": ym}
            )
        return [
            {"month": ym, "block_count": len(blks), "file_version": 1, "blocks": blks}
            for ym, blks in sorted(by_month.items())
        ]
