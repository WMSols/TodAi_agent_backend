"""Environment flags and Supabase connection settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

__all__ = [
    "REPO_ROOT",
    "DEFAULT_SANDBOX_USER_ID",
    "SESSION_MEMORY_PREFIX",
    "auth_dev_allow_default",
    "firebase_configured",
    "firebase_project_id",
    "firebase_service_account_info",
    "local_auth_secret",
    "local_auth_configured",
    "storage_backend_label",
    "supabase_configured",
    "supabase_url",
    "supabase_anon_key",
    "supabase_service_role_key",
    "seed_dir",
    "server_port",
    "cors_allowed_origins",
]

# Sandbox user id in API → fixed UUID in Postgres when not authenticated.
DEFAULT_SANDBOX_USER_ID = "00000000-0000-0000-0000-000000000001"

SESSION_MEMORY_PREFIX = "TODAI_SESSION::"


def supabase_configured() -> bool:
    return bool(supabase_url() and supabase_service_role_key())


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").strip()


def supabase_anon_key() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "").strip()


def supabase_service_role_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def firebase_project_id() -> str:
    return os.environ.get("FIREBASE_PROJECT_ID", "").strip()


def firebase_service_account_info() -> dict | None:
    """Parse service account JSON from env or file path."""
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            path = Path(raw)
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return data if isinstance(data, dict) else None
                except (OSError, json.JSONDecodeError):
                    return None
            return None
    path_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if not path_raw:
        path_raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path_raw:
        path = Path(path_raw)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else None
            except (OSError, json.JSONDecodeError):
                return None
    return None


def firebase_configured() -> bool:
    return bool(firebase_project_id() and firebase_service_account_info())


def auth_dev_allow_default() -> bool:
    """When True, missing Authorization uses sandbox user even if auth is configured."""
    return os.environ.get("AUTH_DEV_ALLOW_DEFAULT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def local_auth_secret() -> str:
    return os.environ.get("LOCAL_AUTH_SECRET", "").strip()


def local_auth_configured() -> bool:
    return bool(local_auth_secret()) and supabase_configured()


def storage_backend_label() -> str:
    return "supabase"


def seed_dir() -> Path:
    """Default calendar/profile JSON used only to seed new Supabase users."""
    return Path(__file__).resolve().parent / "seed" / "default"


def server_port(default: int = 8000) -> int:
    """Bind port: Render sets PORT; local dev may use TODAI_PORT."""
    for key in ("PORT", "TODAI_PORT"):
        raw = os.environ.get(key, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
    return default


def cors_allowed_origins() -> list[str]:
    """
    Comma-separated CORS_ORIGINS for external web clients (Flutter web, separate SPA).
    Empty = no CORS middleware (bundled UI on same host still works).
    Use * to allow any origin (no credentials).
    """
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]
