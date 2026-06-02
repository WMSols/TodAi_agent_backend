from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from todai.database.config import seed_dir
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.helpers import _parse_uuid
from todai.database.utils.json_io import read_json

if TYPE_CHECKING:
    from todai.database.repositories.supabase.calendar import SupabaseCalendarRepository

log = logging.getLogger("todai.supabase.profile")


class SupabaseProfileRepository:
    def __init__(self, ctx: SupabaseContext):
        self._ctx = ctx

    def tz_name(self) -> str:
        if self._ctx.tz:
            return self._ctx.tz
        row = (
            self._ctx.client.table("users")
            .select("timezone")
            .eq("id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        data = (row.data or [{}])[0] if row.data else {}
        self._ctx.tz = (data.get("timezone") or "UTC").strip() or "UTC"
        return self._ctx.tz

    def ensure_user(
        self,
        calendar: SupabaseCalendarRepository | None = None,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        existing = (
            self._ctx.client.table("users")
            .select("id")
            .eq("id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            if email or display_name:
                patch: dict[str, Any] = {}
                if email:
                    patch["email"] = email
                if display_name:
                    patch["display_name"] = display_name
                if patch:
                    self._ctx.client.table("users").update(patch).eq(
                        "id", self._ctx.db_user_id
                    ).execute()
            return
        seed_path = seed_dir() / "profile.json"
        seed = read_json(seed_path) or {}
        tz = (seed.get("timezone") or "UTC").strip() or "UTC"
        name = (display_name or "").strip() or seed.get("display_name") or "User"
        row: dict[str, Any] = {
            "id": self._ctx.db_user_id,
            "display_name": name,
            "timezone": tz,
            "locale": "en",
            "status": "active",
        }
        if email:
            row["email"] = email
        self._ctx.client.table("users").insert(row).execute()
        wh = seed.get("working_hours") or {}
        settings: dict[str, Any] = {
            "user_id": self._ctx.db_user_id,
            "default_event_duration_minutes": 60,
        }
        if wh.get("start"):
            settings["working_day_start"] = str(wh["start"])[:8]
        if wh.get("end"):
            settings["working_day_end"] = str(wh["end"])[:8]
        self._ctx.client.table("user_settings").upsert(settings).execute()
        self._seed_goals(seed)
        if calendar is not None:
            calendar.seed_from_files()
        self._ctx.tz = tz
        log.info("Created Supabase user id=%s (api=%s)", self._ctx.db_user_id, self._ctx.api_user_id)

    def _seed_goals(self, seed: dict[str, Any]) -> None:
        for g in seed.get("goals") or []:
            if not isinstance(g, dict) or not g.get("title"):
                continue
            gid = _parse_uuid(str(g.get("id", ""))) or str(uuid.uuid4())
            row: dict[str, Any] = {
                "id": gid,
                "user_id": self._ctx.db_user_id,
                "title": g["title"],
                "status": g.get("status") or "active",
                "difficulty": "medium",
            }
            if g.get("deadline"):
                row["target_date"] = str(g["deadline"])[:10]
            try:
                self._ctx.client.table("goals").upsert(row).execute()
            except Exception as e:
                log.warning("goal seed skip %s: %s", gid, e)

    def profile_exists(self) -> bool:
        row = (
            self._ctx.client.table("users")
            .select("id")
            .eq("id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        return bool(row.data)

    def read_profile(self) -> dict[str, Any]:
        u = (
            self._ctx.client.table("users")
            .select("*")
            .eq("id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        if not u.data:
            raise FileNotFoundError(f"Missing user: {self._ctx.db_user_id}")
        user = u.data[0]
        self._ctx.tz = user.get("timezone") or "UTC"
        settings_row: dict[str, Any] = {}
        s = (
            self._ctx.client.table("user_settings")
            .select("*")
            .eq("user_id", self._ctx.db_user_id)
            .limit(1)
            .execute()
        )
        if s.data:
            settings_row = s.data[0]
        goals_resp = (
            self._ctx.client.table("goals")
            .select("id, title, status, target_date, difficulty")
            .eq("user_id", self._ctx.db_user_id)
            .execute()
        )
        goals = [
            {
                "id": str(g["id"]),
                "title": g["title"],
                "deadline": g.get("target_date"),
                "priority": g.get("difficulty") or "medium",
                "status": g.get("status") or "active",
            }
            for g in (goals_resp.data or [])
        ]
        wh_start = settings_row.get("working_day_start")
        wh_end = settings_row.get("working_day_end")
        working_hours: dict[str, Any] = {}
        if wh_start:
            working_hours["start"] = str(wh_start)[:5]
        if wh_end:
            working_hours["end"] = str(wh_end)[:5]
        return {
            "user_id": self._ctx.api_user_id,
            "display_name": user.get("display_name"),
            "timezone": self._ctx.tz,
            "working_hours": working_hours,
            "preferences": {},
            "goals": goals,
        }

    def write_profile(self, data: dict[str, Any]) -> None:
        tz = (data.get("timezone") or self.tz_name() or "UTC").strip()
        self._ctx.client.table("users").update(
            {
                "display_name": data.get("display_name"),
                "timezone": tz,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", self._ctx.db_user_id).execute()
        self._ctx.tz = tz
        wh = data.get("working_hours") or {}
        patch: dict[str, Any] = {"user_id": self._ctx.db_user_id}
        if wh.get("start"):
            patch["working_day_start"] = str(wh["start"])
        if wh.get("end"):
            patch["working_day_end"] = str(wh["end"])
        if len(patch) > 1:
            self._ctx.client.table("user_settings").upsert(patch).execute()
