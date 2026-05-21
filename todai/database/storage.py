"""
storage.py — persistence, config, API models, logging, user reset

Sections:
  1. Config (.env, Groq, data paths)
  2. Pydantic request/response models for FastAPI
  3. JSON file I/O + per-user file lock (UserStore)
  4. Logging + terminal mirror of API responses
  5. Reset user folder from seed bundle
  6. parse_server_date — "today" from storage index
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from filelock import FileLock
from pydantic import BaseModel, Field

# ── 1. Config ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.environ.get("TODAI_DATA_DIR", str(REPO_ROOT / "data")))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_groq_ctx = os.environ.get("GROQ_CONTEXT_WINDOW_TOKENS", "").strip()
GROQ_CONTEXT_WINDOW_TOKENS: int | None = int(_groq_ctx) if _groq_ctx.isdigit() else None


def planner_mode() -> str:
    return "groq" if GROQ_API_KEY else "mock"


# ── 2. API models ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_id: str = "default"
    message: str = Field(..., min_length=1, max_length=8000)


class ConfirmRequest(BaseModel):
    user_id: str = "default"


class RejectRequest(BaseModel):
    user_id: str = "default"


class ResetRequest(BaseModel):
    user_id: str = "default"


class ChatResponse(BaseModel):
    assistant_text: str
    state: str
    schedule_version: int
    pending_proposal_id: str | None = None
    agent_mode: str | None = None
    reply_text: str | None = None
    suggested_action: str | None = None
    agent_state: str | None = None
    schedule_display: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    validator_errors: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    api_usage: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.reply_text is None:
            object.__setattr__(self, "reply_text", self.assistant_text)
        if self.agent_state is None:
            object.__setattr__(self, "agent_state", self.agent_mode)


# ── 3. User JSON store ────────────────────────────────────────────────────

def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class UserPaths:
    def __init__(self, data_dir: Path, user_id: str):
        self.user_id = user_id
        self.root = data_dir / "users" / user_id
        self.profile = self.root / "profile.json"
        self.chat = self.root / "chat.json"

    def calendar_path(self, year_month: str) -> Path:
        return self.root / f"calendar_{year_month}.json"

    def lock_path(self) -> Path:
        return self.root / ".user.lock"


class UserStore:
    """Per-user file lock + atomic JSON reads/writes under data/users/<id>/."""

    def __init__(self, data_dir: Path, user_id: str):
        self.paths = UserPaths(data_dir, user_id)
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.paths.lock_path()), timeout=30)

    def __enter__(self) -> UserStore:
        self._lock.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._lock.release()

    def read_profile(self) -> dict[str, Any]:
        data = read_json(self.paths.profile)
        if not data:
            raise FileNotFoundError(f"Missing profile: {self.paths.profile}")
        return data

    def write_profile(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.paths.profile, data)

    def read_chat(self) -> dict[str, Any]:
        data = read_json(self.paths.chat)
        if not data:
            return {
                "conversation_id": self.paths.user_id,
                "state": "idle",
                "schedule_version": 1,
                "pending_proposal_id": None,
                "pending_proposal": None,
                "last_turn_id": None,
                "messages": [],
            }
        return data

    def write_chat(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.paths.chat, data)

    def read_calendar_month(self, year_month: str) -> dict[str, Any]:
        data = read_json(self.paths.calendar_path(year_month))
        if not data:
            return {"month": year_month, "version": 1, "blocks": []}
        return data

    def write_calendar_month(self, year_month: str, data: dict[str, Any]) -> None:
        atomic_write_json(self.paths.calendar_path(year_month), data)

    def planner_storage_index(self) -> dict[str, Any]:
        profile_tip: dict[str, Any] = {}
        try:
            pr = self.read_profile()
            profile_tip = {"display_name": pr.get("display_name"), "timezone": pr.get("timezone")}
        except FileNotFoundError:
            pass

        calendar_rows: list[dict[str, Any]] = []
        for p in sorted(self.paths.root.glob("calendar_*.json")):
            ym = p.stem.removeprefix("calendar_")
            if len(ym) != 7 or ym[4] != "-":
                continue
            doc = read_json(p) or {}
            blocks = doc.get("blocks") or []
            calendar_rows.append(
                {
                    "month": ym,
                    "block_count": len(blocks),
                    "file_version": int(doc.get("version", 1)),
                    "blocks": [{"id": b.get("id"), "title": b.get("title"), "month": ym} for b in blocks if b.get("id")],
                }
            )
        flat_ids = [b["id"] for row in calendar_rows for b in row.get("blocks") or [] if b.get("id")]
        now = datetime.now(timezone.utc)
        return {
            "user_id": self.paths.user_id,
            "server_date_utc": now.date().isoformat(),
            "server_datetime_utc": now.isoformat(timespec="minutes"),
            "profile_path_exists": self.paths.profile.exists(),
            "profile": profile_tip,
            "chat_path_exists": self.paths.chat.exists(),
            "calendar_files": calendar_rows,
            "years_with_calendar_json": sorted({row["month"][:4] for row in calendar_rows}),
            "known_block_ids": flat_ids,
        }


# ── 4. Logging ────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    explicit = os.environ.get("TODAI_LOG_LEVEL", "").strip()
    terminal_debug = os.environ.get("TODAI_TERMINAL_DEBUG", "1").strip().lower() in ("1", "true", "yes")
    level_name = explicit.upper() if explicit else ("DEBUG" if terminal_debug else "INFO")
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    log = logging.getLogger("todai")
    log.info(
        "Logging level=%s (TODAI_LOG_LEVEL=%s, TODAI_TERMINAL_DEBUG=%s)",
        logging.getLevelName(level),
        explicit or "(default)",
        "on" if terminal_debug else "off",
    )
    return log


logger = setup_logging()
_TERMINAL_DEBUG = os.environ.get("TODAI_TERMINAL_DEBUG", "1").strip().lower() in ("1", "true", "yes")


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
    errs = data.get("validator_errors") or []
    has_issue = state == "error" or errs

    parts = [endpoint, f"user={user_id}"]
    if route:
        parts.append(f"intent={route}")
    if mode:
        parts.append(f"mode={mode}")
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


# ── 5. User reset ─────────────────────────────────────────────────────────

def seed_dir() -> Path:
    return Path(__file__).resolve().parent / "seed" / "default"


def reset_user_to_seed(data_dir: Path, user_id: str) -> dict[str, Any]:
    sd = seed_dir()
    if not sd.is_dir():
        return {"ok": False, "user_id": user_id, "detail": "missing_seed_dir", "path": str(sd)}

    with UserStore(data_dir, user_id) as store:
        root = store.paths.root
        for p in root.glob("calendar_*.json"):
            p.unlink(missing_ok=True)
        copied: list[str] = []
        for src in sorted(sd.iterdir()):
            if src.is_file() and src.suffix.lower() == ".json" and src.name != "chat.json":
                shutil.copy2(src, root / src.name)
                copied.append(src.name)
        store.write_chat(
            {
                "conversation_id": user_id,
                "state": "idle",
                "schedule_version": 1,
                "pending_proposal_id": None,
                "pending_proposal": None,
                "last_turn_id": None,
                "messages": [],
            }
        )
    return {
        "ok": True,
        "user_id": user_id,
        "message": "Calendar and chat reset to sandbox defaults.",
        "restored_files": copied,
    }


# ── 6. Server date helper ─────────────────────────────────────────────────

def parse_server_date(storage_index: dict[str, Any] | None) -> date:
    raw = (storage_index or {}).get("server_date_utc") or ""
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    return datetime.now().date()
