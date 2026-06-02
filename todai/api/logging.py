"""Application logging and API response terminal mirror."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from todai.database.models.api import ChatResponse

_TERMINAL_DEBUG = os.environ.get("TODAI_TERMINAL_DEBUG", "0").strip().lower() in ("1", "true", "yes")


def setup_logging() -> logging.Logger:
    explicit = os.environ.get("TODAI_LOG_LEVEL", "").strip()
    terminal_debug = _TERMINAL_DEBUG
    level_name = explicit.upper() if explicit else ("DEBUG" if terminal_debug else "INFO")
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    for name in ("httpx", "httpcore", "hpack", "h2", "urllib3", "supabase"):
        logging.getLogger(name).setLevel(logging.WARNING)
    log = logging.getLogger("todai")
    log.info(
        "Logging level=%s (TODAI_LOG_LEVEL=%s, TODAI_TERMINAL_DEBUG=%s)",
        logging.getLevelName(level),
        explicit or "(default)",
        "on" if terminal_debug else "off",
    )
    return log


logger = setup_logging()


def _truncate(text: str, limit: int = 1200) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit] + "…"


def log_api_response(
    endpoint: str,
    *,
    user_id: str,
    resp: ChatResponse | dict[str, Any],
    user_message: str | None = None,
) -> None:
    """One line per API call in the terminal (matches UI response log style)."""
    data = resp.model_dump() if isinstance(resp, ChatResponse) else dict(resp)
    state = str(data.get("state") or "")
    assistant = _truncate(str(data.get("assistant_text") or data.get("reply_text") or ""), 160)
    debug = data.get("debug") if isinstance(data.get("debug"), dict) else {}
    route = debug.get("intent") or debug.get("route") or ""
    planner = debug.get("planner") or ""
    mode = data.get("agent_mode") or data.get("agent_state") or ""
    phase = data.get("phase") or debug.get("phase") or ""
    plan_id = data.get("plan_id") or ""
    errs = data.get("validator_errors") or []
    has_issue = state == "error" or errs

    parts = [endpoint, f"user={user_id}"]
    if route:
        parts.append(f"intent={route}")
    if mode:
        parts.append(f"mode={mode}")
    if phase:
        parts.append(f"phase={phase}")
    if plan_id:
        parts.append(f"plan={str(plan_id)[:8]}")
    if planner:
        parts.append(f"planner={planner}")
    if state != "idle":
        parts.append(f"state={state}")
    if user_message:
        parts.append(f"in={_truncate(user_message, 80)!r}")
    if assistant:
        parts.append(f"out={assistant!r}")
    if errs:
        parts.append(f"errors={len(errs)}")
    usage = data.get("api_usage") if isinstance(data.get("api_usage"), dict) else debug.get("api_usage")
    if isinstance(usage, dict):
        parts.append(
            "groq="
            f"{usage.get('turn_requests', 0)}req/msg,"
            f"rpm={usage.get('rpm_used', 0)}/{usage.get('rpm_limit', '?')},"
            f"tpm={usage.get('tpm_used', 0)}/{usage.get('tpm_limit', '?')}"
        )
        if usage.get("rate_limited") and usage.get("retry_after_seconds"):
            parts.append(f"wait={usage.get('retry_after_seconds')}s")

    line = " | ".join(parts)
    if has_issue:
        logger.error("TodAI %s", line)
        if errs and _TERMINAL_DEBUG:
            logger.error("  %s", json.dumps(errs, default=str)[:500])
    else:
        logger.info("TodAI %s", line)
