"""Goal plan session persistence (Supabase + message buckets)."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

from todai.database.buckets import CHANNEL_GOAL_PLAN, goal_bucket_limits
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.message_buckets import SupabaseMessageBucketStore
from todai.database.repositories.supabase.helpers import get_supabase_client, resolve_db_user_id

GOAL_PLAN_DAYS = 7
SESSION_PREFIX = "TODAI_GOAL_PLAN::"


def _plan_session_key(plan_id: str) -> str:
    return f"{SESSION_PREFIX}{plan_id}"


class GoalPlanSessionStore:
    """Plan FSM + draft fields in agent_memories; messages in goal_plan buckets."""

    def __init__(self, user_id: str):
        self.api_user_id = user_id
        self.db_user_id = resolve_db_user_id(user_id)
        self._client = get_supabase_client()

    def update_goal(
        self,
        goal_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        patch: dict[str, Any] = {"updated_at": "now()"}
        if title is not None:
            patch["title"] = (title or "New goal").strip()[:200]
        if description is not None:
            patch["description"] = (description or "").strip() or None
        if len(patch) > 1:
            self._client.table("goals").update(patch).eq("id", goal_id).eq(
                "user_id", self.db_user_id
            ).execute()

    def create_plan(self, *, title: str, description: str = "") -> dict[str, Any]:
        goal_ins = (
            self._client.table("goals")
            .insert(
                {
                    "user_id": self.db_user_id,
                    "title": title.strip() or "New goal",
                    "description": description.strip() or None,
                    "difficulty": "medium",
                    "status": "active",
                }
            )
            .execute()
        )
        goal_id = str(goal_ins.data[0]["id"])
        start = date.today()
        end = start + timedelta(days=GOAL_PLAN_DAYS - 1)
        plan_ins = (
            self._client.table("goal_week_plans")
            .insert(
                {
                    "user_id": self.db_user_id,
                    "goal_id": goal_id,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "difficulty": "medium",
                    "status": "draft",
                    "plan_notes": description.strip() or None,
                }
            )
            .execute()
        )
        plan_id = str(plan_ins.data[0]["id"])
        ctx = SupabaseContext(
            client=self._client,
            api_user_id=self.api_user_id,
            db_user_id=self.db_user_id,
        )
        bucket_store = SupabaseMessageBucketStore(ctx, channel=CHANNEL_GOAL_PLAN)
        bucket_store._get_or_create_conversation(goal_week_plan_id=plan_id)

        session = {
            "phase": "interrogate",
            "intake_step": "objective",
            "answers": {},
            "goal_id": goal_id,
            "title": title.strip(),
            "description": description.strip(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        session["intake_style"] = "ai"
        self._save_plan_session(plan_id, session)
        return {
            "plan_id": plan_id,
            "goal_id": goal_id,
            "status": "draft",
            "phase": "interrogate",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "title": title,
            "storage": "supabase",
        }

    def _save_plan_session(self, plan_id: str, session: dict[str, Any]) -> None:
        key = _plan_session_key(plan_id)
        rows = (
            self._client.table("agent_memories")
            .select("id")
            .eq("user_id", self.db_user_id)
            .eq("memory_type", "fact")
            .eq("source", "agent")
            .like("content", f"{key}%")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        payload = {**session, "plan_id": plan_id}
        content = key + json.dumps(payload, ensure_ascii=False)
        if rows.data:
            self._client.table("agent_memories").update({"content": content}).eq(
                "id", rows.data[0]["id"]
            ).execute()
        else:
            self._client.table("agent_memories").insert(
                {
                    "user_id": self.db_user_id,
                    "memory_type": "fact",
                    "source": "agent",
                    "content": content,
                    "importance": 3,
                }
            ).execute()

    def _load_plan_session(self, plan_id: str) -> dict[str, Any]:
        key = _plan_session_key(plan_id)
        rows = (
            self._client.table("agent_memories")
            .select("content")
            .eq("user_id", self.db_user_id)
            .eq("memory_type", "fact")
            .eq("source", "agent")
            .like("content", f"{key}%")
            .limit(1)
            .execute()
        )
        if not rows.data:
            return {}
        raw = rows.data[0].get("content") or ""
        if not raw.startswith(key):
            return {}
        try:
            return json.loads(raw[len(key) :])
        except json.JSONDecodeError:
            return {}

    def list_messages(self, plan_id: str) -> list[dict[str, Any]]:
        ctx = SupabaseContext(
            client=self._client,
            api_user_id=self.api_user_id,
            db_user_id=self.db_user_id,
        )
        conv = (
            self._client.table("conversations")
            .select("id")
            .eq("user_id", self.db_user_id)
            .eq("channel", CHANNEL_GOAL_PLAN)
            .eq("goal_week_plan_id", plan_id)
            .limit(1)
            .execute()
        )
        if not conv.data:
            return []
        ctx.conversation_id = str(conv.data[0]["id"])
        return SupabaseMessageBucketStore(ctx, channel=CHANNEL_GOAL_PLAN).list_messages()

    def append_turn(
        self,
        plan_id: str,
        *,
        user_message: str,
        assistant_message: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        ctx = SupabaseContext(
            client=self._client,
            api_user_id=self.api_user_id,
            db_user_id=self.db_user_id,
        )
        conv = (
            self._client.table("conversations")
            .select("id")
            .eq("goal_week_plan_id", plan_id)
            .limit(1)
            .execute()
        )
        if not conv.data:
            return
        ctx.conversation_id = str(conv.data[0]["id"])
        store = SupabaseMessageBucketStore(ctx, channel=CHANNEL_GOAL_PLAN)
        limits = goal_bucket_limits()
        msgs = store.list_messages()
        msgs.append({"role": "user", "content": user_message})
        ameta = meta or {}
        msgs.append({"role": "assistant", "content": assistant_message, "meta": ameta})
        store.replace_messages(msgs, limits=limits, goal_week_plan_id=plan_id)

    def get_plan_row(self, plan_id: str) -> dict[str, Any] | None:
        rows = (
            self._client.table("goal_week_plans")
            .select("id, goal_id, start_date, end_date, status, difficulty")
            .eq("id", plan_id)
            .eq("user_id", self.db_user_id)
            .limit(1)
            .execute()
        )
        return rows.data[0] if rows.data else None

    def insert_goal_tasks(
        self,
        plan_id: str,
        goal_id: str,
        task_rows: list[dict[str, Any]],
    ) -> int:
        if not task_rows:
            return 0
        self._client.table("goal_tasks").delete().eq("plan_id", plan_id).execute()
        payload = []
        for row in task_rows:
            payload.append(
                {
                    "user_id": self.db_user_id,
                    "goal_id": goal_id,
                    "plan_id": plan_id,
                    "title": row["title"],
                    "description": row.get("description"),
                    "task_date": row["task_date"],
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "status": "pending",
                    "sort_order": int(row.get("sort_order") or 0),
                    "source": "agent",
                }
            )
        self._client.table("goal_tasks").insert(payload).execute()
        return len(payload)

    def list_goal_tasks(self, plan_id: str) -> list[dict[str, Any]]:
        rows = (
            self._client.table("goal_tasks")
            .select(
                "id, title, description, task_date, start_time, end_time, status, sort_order"
            )
            .eq("plan_id", plan_id)
            .eq("user_id", self.db_user_id)
            .order("task_date")
            .order("sort_order")
            .execute()
        )
        return list(rows.data or [])

    def list_goal_tasks_in_range(self, start: date, end: date) -> list[dict[str, Any]]:
        """All goal tasks for this user between start and end (inclusive)."""
        rows = (
            self._client.table("goal_tasks")
            .select(
                "id, title, description, task_date, start_time, end_time, status, "
                "sort_order, plan_id, goal_id"
            )
            .eq("user_id", self.db_user_id)
            .gte("task_date", start.isoformat())
            .lte("task_date", end.isoformat())
            .order("task_date")
            .order("sort_order")
            .execute()
        )
        return list(rows.data or [])

    def get_goal_task(self, task_id: str) -> dict[str, Any] | None:
        rows = (
            self._client.table("goal_tasks")
            .select(
                "id, title, description, task_date, start_time, end_time, status, "
                "sort_order, plan_id, goal_id"
            )
            .eq("id", task_id)
            .eq("user_id", self.db_user_id)
            .limit(1)
            .execute()
        )
        data = rows.data or []
        return data[0] if data else None

    def update_goal_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        task_date: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if not self.get_goal_task(task_id):
            raise LookupError("goal task not found")
        patch: dict[str, Any] = {}
        if title is not None:
            patch["title"] = (title or "Goal task").strip()[:200]
        if description is not None:
            patch["description"] = (description or "").strip() or None
        if task_date is not None:
            patch["task_date"] = date.fromisoformat(task_date[:10]).isoformat()
        if start_time is not None:
            patch["start_time"] = (start_time or "").strip() or None
        if end_time is not None:
            patch["end_time"] = (end_time or "").strip() or None
        if status is not None:
            st = (status or "pending").strip().lower()
            if st in ("completed",):
                st = "done"
            patch["status"] = st
        if not patch:
            row = self.get_goal_task(task_id)
            assert row is not None
            return row
        (
            self._client.table("goal_tasks")
            .update(patch)
            .eq("id", task_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        row = self.get_goal_task(task_id)
        assert row is not None
        return row

    def delete_goal_task(self, task_id: str) -> dict[str, Any]:
        if not self.get_goal_task(task_id):
            raise LookupError("goal task not found")
        self._client.table("goal_tasks").delete().eq("id", task_id).eq(
            "user_id", self.db_user_id
        ).execute()
        return {"ok": True, "deleted": task_id}

    def list_user_goals(self) -> list[dict[str, Any]]:
        rows = (
            self._client.table("goals")
            .select("id, title, description, difficulty, status, created_at")
            .eq("user_id", self.db_user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return list(rows.data or [])

    def list_user_plans(self) -> list[dict[str, Any]]:
        rows = (
            self._client.table("goal_week_plans")
            .select("id, goal_id, start_date, end_date, status, difficulty, plan_notes")
            .eq("user_id", self.db_user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return list(rows.data or [])

    def delete_goal_for_plan(self, plan_id: str) -> dict[str, Any]:
        """Remove goal + all its week plans, tasks, chat, and session memory (not other goals)."""
        plan_row = self.get_plan_row(plan_id)
        if not plan_row:
            return {"ok": False, "plan_id": plan_id, "error": "plan_not_found"}
        goal_id = str(plan_row["goal_id"])

        plans = (
            self._client.table("goal_week_plans")
            .select("id")
            .eq("goal_id", goal_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        plan_ids = [str(p["id"]) for p in (plans.data or [])]

        tasks = (
            self._client.table("goal_tasks")
            .delete()
            .eq("goal_id", goal_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        task_count = len(tasks.data or [])

        for pid in plan_ids:
            conv = (
                self._client.table("conversations")
                .select("id")
                .eq("goal_week_plan_id", pid)
                .execute()
            )
            for row in conv.data or []:
                cid = str(row["id"])
                self._client.table("message_buckets").delete().eq(
                    "conversation_id", cid
                ).execute()
                self._client.table("conversations").delete().eq("id", cid).execute()
            key = _plan_session_key(pid)
            mem = (
                self._client.table("agent_memories")
                .select("id")
                .eq("user_id", self.db_user_id)
                .like("content", f"{key}%")
                .execute()
            )
            for mrow in mem.data or []:
                self._client.table("agent_memories").delete().eq(
                    "id", mrow["id"]
                ).execute()

        plans_del = (
            self._client.table("goal_week_plans")
            .delete()
            .eq("goal_id", goal_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        goal_del = (
            self._client.table("goals")
            .delete()
            .eq("id", goal_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        return {
            "ok": True,
            "goal_id": goal_id,
            "plan_id": plan_id,
            "tasks_deleted": task_count,
            "plans_deleted": len(plans_del.data or []),
            "goals_deleted": len(goal_del.data or []),
        }

    def delete_plan(self, plan_id: str) -> dict[str, Any]:
        tasks = (
            self._client.table("goal_tasks")
            .delete()
            .eq("plan_id", plan_id)
            .eq("user_id", self.db_user_id)
            .execute()
        )
        task_count = len(tasks.data or [])
        self._client.table("goal_week_plans").update({"status": "draft"}).eq(
            "id", plan_id
        ).execute()
        key = _plan_session_key(plan_id)
        mem = (
            self._client.table("agent_memories")
            .select("id")
            .eq("user_id", self.db_user_id)
            .like("content", f"{key}%")
            .execute()
        )
        for row in mem.data or []:
            self._client.table("agent_memories").delete().eq("id", row["id"]).execute()
        return {"tasks_deleted": task_count, "plan_id": plan_id, "plan_status": "draft"}

    def delete_all_user_goal_data(self) -> dict[str, Any]:
        t = (
            self._client.table("goal_tasks")
            .delete()
            .eq("user_id", self.db_user_id)
            .execute()
        )
        p = (
            self._client.table("goal_week_plans")
            .delete()
            .eq("user_id", self.db_user_id)
            .execute()
        )
        g = (
            self._client.table("goals")
            .delete()
            .eq("user_id", self.db_user_id)
            .execute()
        )
        return {
            "tasks_deleted": len(t.data or []),
            "plans_deleted": len(p.data or []),
            "goals_deleted": len(g.data or []),
        }

    def _conv_id_for_plan(self, plan_id: str) -> str | None:
        rows = (
            self._client.table("conversations")
            .select("id")
            .eq("goal_week_plan_id", plan_id)
            .limit(1)
            .execute()
        )
        return str(rows.data[0]["id"]) if rows.data else None

    def update_plan_after_create(
        self,
        plan_id: str,
        goal_id: str,
        *,
        difficulty: str,
        plan_notes: str,
    ) -> None:
        self._client.table("goal_week_plans").update(
            {"status": "active", "difficulty": difficulty, "plan_notes": plan_notes}
        ).eq("id", plan_id).execute()
        self._client.table("goals").update(
            {"difficulty": difficulty, "description": plan_notes}
        ).eq("id", goal_id).execute()
