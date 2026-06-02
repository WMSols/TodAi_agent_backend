"""Persist chat UX messages in rolling buckets (Supabase)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from todai.database.buckets import (
    CHANNEL_CHAT,
    CHANNEL_GOAL_PLAN,
    BucketLimits,
    MessageChannel,
    normalize_message_row,
)
from todai.database.repositories.supabase.context import SupabaseContext


class SupabaseMessageBucketStore:
    def __init__(self, ctx: SupabaseContext, *, channel: MessageChannel = CHANNEL_CHAT):
        self._ctx = ctx
        self._channel = channel

    def _get_or_create_conversation(
        self,
        *,
        goal_week_plan_id: str | None = None,
    ) -> str:
        if self._ctx.conversation_id:
            return self._ctx.conversation_id

        q = (
            self._ctx.client.table("conversations")
            .select("id")
            .eq("user_id", self._ctx.db_user_id)
            .eq("channel", self._channel)
            .is_("archived_at", "null")
        )
        if goal_week_plan_id:
            q = q.eq("goal_week_plan_id", goal_week_plan_id)
        else:
            q = q.is_("goal_week_plan_id", "null")

        rows = q.order("created_at", desc=True).limit(1).execute()
        if rows.data:
            self._ctx.conversation_id = str(rows.data[0]["id"])
            return self._ctx.conversation_id

        payload: dict[str, Any] = {
            "user_id": self._ctx.db_user_id,
            "title": "TodAI Goal plan" if self._channel == CHANNEL_GOAL_PLAN else "TodAI",
            "channel": self._channel,
        }
        if goal_week_plan_id:
            payload["goal_week_plan_id"] = goal_week_plan_id
        ins = self._ctx.client.table("conversations").insert(payload).execute()
        self._ctx.conversation_id = str(ins.data[0]["id"])
        return self._ctx.conversation_id

    def _get_or_create_active_bucket(self, conversation_id: str) -> str:
        rows = (
            self._ctx.client.table("message_buckets")
            .select("id")
            .eq("conversation_id", conversation_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if rows.data:
            return str(rows.data[0]["id"])

        ins = (
            self._ctx.client.table("message_buckets")
            .insert(
                {
                    "conversation_id": conversation_id,
                    "user_id": self._ctx.db_user_id,
                    "channel": self._channel,
                    "bucket_index": 0,
                    "is_active": True,
                }
            )
            .execute()
        )
        return str(ins.data[0]["id"])

    def list_messages(self, *, conversation_id: str | None = None) -> list[dict[str, Any]]:
        conv_id = conversation_id or self._get_or_create_conversation()
        bucket_id = self._get_or_create_active_bucket(conv_id)
        rows = (
            self._ctx.client.table("messages")
            .select("id, role, content, meta, sequence_no, created_at")
            .eq("bucket_id", bucket_id)
            .order("sequence_no")
            .execute()
        )
        out: list[dict[str, Any]] = []
        for m in rows.data or []:
            row: dict[str, Any] = {
                "role": m["role"],
                "content": m.get("content") or "",
            }
            if m.get("meta"):
                row["meta"] = m["meta"] if isinstance(m["meta"], dict) else json.loads(m["meta"])
            out.append(row)
        return out

    def replace_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        limits: BucketLimits,
        conversation_id: str | None = None,
        goal_week_plan_id: str | None = None,
    ) -> None:
        """Sync active bucket to last `limits.store` UX messages (orchestrator-compatible)."""
        conv_id = conversation_id or self._get_or_create_conversation(
            goal_week_plan_id=goal_week_plan_id
        )
        bucket_id = self._get_or_create_active_bucket(conv_id)
        trimmed = limits.trimmed(
            [r for m in messages if (r := normalize_message_row(m)) is not None]
        )

        self._ctx.client.table("messages").delete().eq("bucket_id", bucket_id).execute()

        if not trimmed:
            return

        rows = []
        for seq, m in enumerate(trimmed):
            row: dict[str, Any] = {
                "conversation_id": conv_id,
                "bucket_id": bucket_id,
                "user_id": self._ctx.db_user_id,
                "role": m["role"],
                "content": m["content"],
                "sequence_no": seq,
            }
            if m.get("meta"):
                row["meta"] = m["meta"]
            rows.append(row)

        self._ctx.client.table("messages").insert(rows).execute()
        now = datetime.now(timezone.utc).isoformat()
        self._ctx.client.table("message_buckets").update({"updated_at": now}).eq(
            "id", bucket_id
        ).execute()
        self._ctx.client.table("conversations").update({"last_message_at": now}).eq(
            "id", conv_id
        ).execute()

    def append_message(
        self,
        message: dict[str, Any],
        *,
        limits: BucketLimits,
        conversation_id: str | None = None,
        goal_week_plan_id: str | None = None,
    ) -> None:
        row = normalize_message_row(message)
        if not row:
            return
        current = self.list_messages(conversation_id=conversation_id)
        current.append(row)
        self.replace_messages(
            current,
            limits=limits,
            conversation_id=conversation_id,
            goal_week_plan_id=goal_week_plan_id,
        )
