"""Environment flags and Supabase connection settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

__all__ = [
    "REPO_ROOT",
    "DATA_DIR",
    "DEFAULT_SANDBOX_USER_ID",
    "SESSION_MEMORY_PREFIX",
    "use_local_storage",
    "storage_backend_label",
    "supabase_configured",
    "supabase_url",
    "supabase_anon_key",
    "supabase_service_role_key",
    "seed_dir",
]

DATA_DIR = Path(os.environ.get("TODAI_DATA_DIR", str(REPO_ROOT / "data")))

# Sandbox user id in API → fixed UUID in Postgres when not using local JSON.
DEFAULT_SANDBOX_USER_ID = "00000000-0000-0000-0000-000000000001"

SESSION_MEMORY_PREFIX = "TODAI_SESSION::"


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return None


def use_local_storage() -> bool:
    """True → JSON under data/users/; False → Supabase (requires SUPABASE_* env).

    Defaults to True (local JSON) when LOCAL is unset so existing dev setups keep working.
    """
    for key in ("LOCAL", "TODAI_LOCAL"):
        val = _env_bool(key)
        if val is not None:
            return val
    return True


def supabase_configured() -> bool:
    return bool(supabase_url() and supabase_service_role_key())


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").strip()


def supabase_anon_key() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "").strip()


def supabase_service_role_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def storage_backend_label() -> str:
    return "local" if use_local_storage() else "supabase"


def seed_dir() -> Path:
    return Path(__file__).resolve().parent / "seed" / "default"
