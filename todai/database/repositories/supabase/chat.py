from __future__ import annotations



import json

from typing import Any



from todai.database.buckets import CHANNEL_CHAT, chat_bucket_limits

from todai.database.config import SESSION_MEMORY_PREFIX

from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.message_buckets import SupabaseMessageBucketStore





class SupabaseChatRepository:

    def __init__(self, ctx: SupabaseContext):

        self._ctx = ctx

        self._buckets = SupabaseMessageBucketStore(ctx, channel=CHANNEL_CHAT)



    def chat_exists(self) -> bool:

        return True



    def _conversation_id(self) -> str:

        return self._buckets._get_or_create_conversation()



    def _load_session(self) -> dict[str, Any]:

        rows = (

            self._ctx.client.table("agent_memories")

            .select("id, content")

            .eq("user_id", self._ctx.db_user_id)

            .eq("memory_type", "fact")

            .eq("source", "agent")

            .like("content", f"{SESSION_MEMORY_PREFIX}%")

            .limit(1)

            .execute()

        )

        if not rows.data:

            return {}

        raw = rows.data[0].get("content") or ""

        if not raw.startswith(SESSION_MEMORY_PREFIX):

            return {}

        try:

            return json.loads(raw[len(SESSION_MEMORY_PREFIX) :])

        except json.JSONDecodeError:

            return {}



    def _save_session(self, session: dict[str, Any]) -> None:

        payload = SESSION_MEMORY_PREFIX + json.dumps(session, ensure_ascii=False)

        rows = (

            self._ctx.client.table("agent_memories")

            .select("id")

            .eq("user_id", self._ctx.db_user_id)

            .eq("memory_type", "fact")

            .eq("source", "agent")

            .like("content", f"{SESSION_MEMORY_PREFIX}%")

            .limit(1)

            .execute()

        )

        if rows.data:

            self._ctx.client.table("agent_memories").update({"content": payload}).eq(

                "id", rows.data[0]["id"]

            ).execute()

        else:

            self._ctx.client.table("agent_memories").insert(

                {

                    "user_id": self._ctx.db_user_id,

                    "memory_type": "fact",

                    "source": "agent",

                    "content": payload,

                    "importance": 1,

                }

            ).execute()



    def read_chat(self) -> dict[str, Any]:

        conv_id = self._conversation_id()

        session = self._load_session()

        messages = self._buckets.list_messages(conversation_id=conv_id)

        return {

            "conversation_id": conv_id,

            "state": session.get("state", "idle"),

            "schedule_version": int(session.get("schedule_version", 1)),

            "pending_proposal_id": session.get("pending_proposal_id"),

            "pending_proposal": session.get("pending_proposal"),

            "last_turn_id": session.get("last_turn_id"),

            "last_agent_mode": session.get("last_agent_mode"),

            "messages": messages,

        }



    def write_chat(self, data: dict[str, Any]) -> None:

        conv_id = data.get("conversation_id") or self._conversation_id()

        session = {

            "state": data.get("state", "idle"),

            "schedule_version": int(data.get("schedule_version", 1)),

            "pending_proposal_id": data.get("pending_proposal_id"),

            "pending_proposal": data.get("pending_proposal"),

            "last_turn_id": data.get("last_turn_id"),

            "last_agent_mode": data.get("last_agent_mode"),

        }

        self._save_session(session)

        self._buckets.replace_messages(

            data.get("messages") or [],

            limits=chat_bucket_limits(),

            conversation_id=conv_id,

        )


