"""
Backward-compatible re-exports.

Prefer explicit imports:
  todai.api.logging          — logger, setup_logging
  todai.agent.planner.groq_config — GROQ_* / planner_mode
  todai.database.models      — API + entity shapes
  todai.database.stores      — UserStore, user_store(), reset
  todai.database.config      — Supabase env
  todai.database.utils       — dates, json_io
"""

from __future__ import annotations

from todai.agent.planner.groq_config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_CONTEXT_WINDOW_TOKENS,
    GROQ_MODEL,
    planner_mode,
)
from todai.api.logging import log_api_response, logger, setup_logging
from todai.database.config import REPO_ROOT, seed_dir, storage_backend_label, supabase_configured
from todai.database.models import (
    ChatRequest,
    ChatResponse,
    ResetRequest,
    UserPaths,
)
from todai.database.stores import SupabaseUserStore, UserStore, log_storage_mode, user_store
from todai.database.stores.reset import reset_user_to_seed
from todai.database.utils import (
    atomic_write_json,
    parse_server_date,
    read_json,
    resolve_user_timezone,
    server_date_fields,
    server_now,
)

__all__ = [
    "REPO_ROOT",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GROQ_BASE_URL",
    "GROQ_CONTEXT_WINDOW_TOKENS",
    "ChatRequest",
    "ChatResponse",
    "ResetRequest",
    "UserPaths",
    "UserStore",
    "SupabaseUserStore",
    "user_store",
    "log_storage_mode",
    "atomic_write_json",
    "read_json",
    "planner_mode",
    "resolve_user_timezone",
    "server_now",
    "server_date_fields",
    "parse_server_date",
    "setup_logging",
    "logger",
    "log_api_response",
    "reset_user_to_seed",
    "seed_dir",
    "storage_backend_label",
    "supabase_configured",
]
