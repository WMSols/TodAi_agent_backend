"""
Database package — persistence (models, repositories, stores).

HTTP models used by FastAPI live in ``todai.database.models``.
Groq settings live in ``todai.agent.planner.groq_config``.
Logging lives in ``todai.api.logging``.
"""

from todai.database.config import REPO_ROOT, storage_backend_label, supabase_configured
from todai.database.models import (
    ChatRequest,
    ChatResponse,
    ResetRequest,
)
from todai.database.stores import UserStore, log_storage_mode, user_store
from todai.database.stores.reset import reset_user_to_seed

__all__ = [
    "REPO_ROOT",
    "ChatRequest",
    "ChatResponse",
    "ResetRequest",
    "UserStore",
    "user_store",
    "log_storage_mode",
    "reset_user_to_seed",
    "storage_backend_label",
    "supabase_configured",
]
