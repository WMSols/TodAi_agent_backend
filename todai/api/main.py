"""
main.py — FastAPI HTTP surface + static UI (API layer entry).

Run from repo root (recommended):

    python main.py

Or from anywhere:

    python -m todai.api.main

Routes:
  GET  /              — chat UI
  GET  /health        — liveness
  POST /api/chat      — user message → agent turn
  POST /api/confirm   — legacy stub
  POST /api/reject    — legacy stub
  GET  /api/state     — debug FSM / storage snapshot
  POST /api/reset     — restore seed calendar + clear chat
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow `python todai/api/main.py` — package imports need repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from todai.api.auth import (
    admin_register_user,
    auth_required,
    public_auth_config,
    require_user_with_fallback,
    resolve_user_id,
)
from todai.api.service import bootstrap_user_profile, confirm, get_debug_state, process_chat, reject
from todai.agent.planner.groq_config import planner_mode
from todai.api.logging import log_api_response, logger, setup_logging
from todai.database.config import DATA_DIR, storage_backend_label, use_local_storage
from todai.database.models import ChatRequest, ConfirmRequest, RegisterRequest, RejectRequest, ResetRequest
from todai.database.stores import log_storage_mode
from todai.database.stores.reset import reset_user_to_seed
from todai.api.goal_plan import router as goal_plan_router

setup_logging()
log_storage_mode(logger)
STATIC_DIR = Path(__file__).resolve().parent / "static"
GOAL_STATIC_DIR = STATIC_DIR / "goal_planner"

app = FastAPI(title="TodAI", version="0.1.0")
app.include_router(goal_plan_router)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "planner": planner_mode(),
        "storage": storage_backend_label(),
        "local_json": use_local_storage(),
        "auth_required": auth_required(),
        "data_dir": str(DATA_DIR),
    }


@app.get("/api/auth/config")
def api_auth_config() -> dict:
    return public_auth_config()


@app.post("/api/auth/register")
async def api_auth_register(body: RegisterRequest):
    """Create user in Supabase Auth (no confirmation email). Client signs in after."""
    if use_local_storage():
        raise HTTPException(status_code=400, detail="Registration only when LOCAL=false (Supabase)")
    return await asyncio.to_thread(
        admin_register_user,
        display_name=body.display_name,
        email=body.email,
        password=body.password,
    )


@app.post("/api/auth/bootstrap")
async def api_auth_bootstrap(authorization: str | None = Header(None, alias="Authorization")):
    from todai.api.auth import _bearer_token, verify_supabase_access_token

    user_id = resolve_user_id(authorization=authorization)
    email: str | None = None
    display_name: str | None = None
    token = _bearer_token(authorization)
    if token:
        user = verify_supabase_access_token(token)
        email = user.get("email")
        meta = user.get("user_metadata") or {}
        if isinstance(meta, dict):
            display_name = meta.get("full_name") or meta.get("name") or meta.get("display_name")
    return await asyncio.to_thread(
        bootstrap_user_profile,
        user_id,
        email=email,
        display_name=display_name,
    )


def _user_id_from_chat(
    body: ChatRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


def _user_id_from_confirm(
    body: ConfirmRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


def _user_id_from_reject(
    body: RejectRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


def _user_id_from_reset(
    body: ResetRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


@app.post("/api/chat")
async def api_chat(
    body: ChatRequest,
    user_id: str = Depends(_user_id_from_chat),
):
    try:
        resp = await asyncio.to_thread(process_chat, user_id, body.message)
        log_api_response("chat", user_id=user_id, resp=resp, user_message=body.message)
        return resp
    except FileNotFoundError as e:
        logger.error("chat 404 user=%s: %s", user_id, e)
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception:
        logger.exception("chat failed user=%s", user_id)
        raise


@app.post("/api/confirm")
async def api_confirm(
    body: ConfirmRequest,
    user_id: str = Depends(_user_id_from_confirm),
):
    resp = await asyncio.to_thread(confirm, user_id)
    log_api_response("confirm", user_id=user_id, resp=resp)
    return resp


@app.post("/api/reject")
async def api_reject(
    body: RejectRequest,
    user_id: str = Depends(_user_id_from_reject),
):
    resp = await asyncio.to_thread(reject, user_id)
    log_api_response("reject", user_id=user_id, resp=resp)
    return resp


@app.get("/api/state")
async def api_state(
    light: bool = True,
    user_id: str = Query("default"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    uid = require_user_with_fallback(user_id, authorization)
    return await asyncio.to_thread(get_debug_state, uid, light)


@app.post("/api/reset")
async def api_reset(
    body: ResetRequest,
    user_id: str = Depends(_user_id_from_reset),
):
    return await asyncio.to_thread(reset_user_to_seed, DATA_DIR, user_id)


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="Missing todai/api/static/index.html")
    return FileResponse(index_path)


@app.get("/goals")
def goal_planner_ui():
    index_path = GOAL_STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="Missing goal planner UI")
    return FileResponse(index_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if GOAL_STATIC_DIR.exists():
    app.mount("/goal-static", StaticFiles(directory=str(GOAL_STATIC_DIR)), name="goal-static")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("TODAI_HOST", "0.0.0.0")
    port = int(os.environ.get("TODAI_PORT", "8000"))
    print(f"TodAI — http://127.0.0.1:{port}/", flush=True)
    uvicorn.run(
        "todai.api.main:app",
        host=host,
        port=port,
        reload=os.environ.get("TODAI_RELOAD", "").strip().lower() in ("1", "true", "yes"),
        log_level=os.environ.get("TODAI_LOG_LEVEL", "info").lower(),
    )
