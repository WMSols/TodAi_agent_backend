from __future__ import annotations



from typing import Any



from todai.database.buckets import CHANNEL_CHAT, chat_bucket_limits

from todai.database.models.entities import empty_chat_document

from todai.database.models.paths import UserPaths

from todai.database.repositories.json.buckets import (

    active_bucket_messages,

    ensure_bucket_structure,

    replace_bucket_messages,

    sync_flat_messages,

)

from todai.database.utils.json_io import atomic_write_json, read_json





class JsonChatRepository:

    def __init__(self, paths: UserPaths):

        self._paths = paths



    def read_chat(self) -> dict[str, Any]:

        data = read_json(self._paths.chat)

        if not data:

            return empty_chat_document(self._paths.user_id)

        data = ensure_bucket_structure(data, channel=CHANNEL_CHAT)

        data["messages"] = active_bucket_messages(data)

        return data



    def write_chat(self, data: dict[str, Any]) -> None:

        data = ensure_bucket_structure(dict(data), channel=CHANNEL_CHAT)

        replace_bucket_messages(

            data,

            data.get("messages") or [],

            limits=chat_bucket_limits(),

            channel=CHANNEL_CHAT,

        )

        sync_flat_messages(data)

        atomic_write_json(self._paths.chat, data)



    def chat_exists(self) -> bool:

        return self._paths.chat.exists()


