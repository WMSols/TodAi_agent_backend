"""Reset user data to seed bundle (local JSON or Supabase)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from todai.database.config import seed_dir, use_local_storage
from todai.database.models.entities import empty_chat_document
from todai.database.stores.json_store import JsonUserStore


def reset_user_to_seed(data_dir: Path, user_id: str) -> dict[str, Any]:
    if not use_local_storage():
        from todai.database.repositories.supabase.reset import reset_user_supabase

        return reset_user_supabase(user_id)

    sd = seed_dir()
    if not sd.is_dir():
        return {"ok": False, "user_id": user_id, "detail": "missing_seed_dir", "path": str(sd)}

    with JsonUserStore(data_dir, user_id) as store:
        root = store.paths.root
        for p in root.glob("calendar_*.json"):
            p.unlink(missing_ok=True)
        copied: list[str] = []
        for src in sorted(sd.iterdir()):
            if src.is_file() and src.suffix.lower() == ".json" and src.name != "chat.json":
                shutil.copy2(src, root / src.name)
                copied.append(src.name)
        store.write_chat(empty_chat_document(user_id))
    return {
        "ok": True,
        "user_id": user_id,
        "message": "Calendar and chat reset to sandbox defaults.",
        "restored_files": copied,
        "storage_backend": "local",
    }
