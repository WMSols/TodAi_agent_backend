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
  GET  /api/state     — debug FSM / storage snapshot
  POST /api/reset     — restore seed calendar + clear chat
  GET/POST/PATCH/DELETE /api/calendar/events — direct calendar CRUD
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from todai.api.auth import (
    auth_required,
    profile_from_auth_user,
    public_auth_config,
    require_user_with_fallback,
    resolve_user_id,
    verify_access_token,
    _bearer_token,
)
from todai.api.local_auth import login_local_user, register_local_user
from todai.api.service import bootstrap_user_profile, get_debug_state, process_chat
from todai.agent.planner.groq_config import planner_mode
from todai.api.logging import log_api_response, logger, setup_logging
from todai.database.config import (
    cors_allowed_origins,
    firebase_configured,
    local_auth_configured,
    server_port,
    storage_backend_label,
    supabase_configured,
)
from todai.api.calendar_router import router as calendar_router
from todai.api.goal_plan import router as goal_plan_router
from todai.api.goal_debug_router import router as goal_debug_router
from todai.api.goal_tasks_router import router as goal_tasks_router
from todai.api.openapi_docs import (
    DOC_AUTH_BOOTSTRAP,
    DOC_AUTH_CONFIG,
    DOC_AUTH_LOGIN,
    DOC_AUTH_REGISTER,
    DOC_CHAT,
    DOC_HEALTH,
    DOC_RESET,
    DOC_STATE,
)
from todai.api.openapi_meta import APP_DESCRIPTION, OPENAPI_TAGS
from todai.api.openapi_setup import install_openapi
from todai.api.schemas import (
    AuthConfigResponse,
    AuthTokenResponse,
    BootstrapResponse,
    ErrorDetail,
    HealthResponse,
)
from todai.database.models import ChatRequest, ChatResponse, LoginRequest, RegisterRequest, ResetRequest
from todai.database.stores import log_storage_mode
from todai.database.stores.reset import reset_user_to_seed

setup_logging()
log_storage_mode(logger)
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="TodAI API",
    version="0.1.0",
    description=APP_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    contact={"name": "TodAI Backend", "url": "https://github.com/"},
    license_info={"name": "Proprietary"},
)
install_openapi(app)

_cors_origins = cors_allowed_origins()
if _cors_origins:
    _cors_kwargs: dict[str, object] = {
        "allow_origins": _cors_origins,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if _cors_origins != ["*"]:
        _cors_kwargs["allow_credentials"] = True
    app.add_middleware(CORSMiddleware, **_cors_kwargs)

app.include_router(goal_plan_router)
app.include_router(goal_debug_router)
app.include_router(goal_tasks_router)
app.include_router(calendar_router)


@app.get(
    "/health",
    tags=["system"],
    summary="Health check",
    description=DOC_HEALTH,
    response_model=HealthResponse,
)
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        planner=planner_mode(),
        storage=storage_backend_label(),
        supabase_configured=supabase_configured(),
        firebase_configured=firebase_configured(),
        local_auth_configured=local_auth_configured(),
        auth_required=auth_required(),
    )


@app.get(
    "/api/auth/config",
    tags=["auth"],
    summary="Auth config",
    description=DOC_AUTH_CONFIG,
    response_model=AuthConfigResponse,
)
def api_auth_config() -> AuthConfigResponse:
    return AuthConfigResponse.model_validate(public_auth_config())


@app.post(
    "/api/auth/register",
    tags=["auth"],
    summary="Register (web only)",
    description=DOC_AUTH_REGISTER,
    response_model=AuthTokenResponse,
    responses={
        400: {"model": ErrorDetail},
        503: {"model": ErrorDetail},
    },
)
async def api_auth_register(body: RegisterRequest) -> AuthTokenResponse:
    result = await asyncio.to_thread(
        register_local_user,
        display_name=body.display_name,
        email=body.email,
        password=body.password,
    )
    return AuthTokenResponse.model_validate(result)


@app.post(
    "/api/auth/login",
    tags=["auth"],
    summary="Login (web only)",
    description=DOC_AUTH_LOGIN,
    response_model=AuthTokenResponse,
    responses={401: {"model": ErrorDetail}},
)
async def api_auth_login(body: LoginRequest) -> AuthTokenResponse:
    result = await asyncio.to_thread(
        login_local_user,
        username=body.username,
        password=body.password,
    )
    return AuthTokenResponse.model_validate(result)


@app.post(
    "/api/auth/bootstrap",
    tags=["auth"],
    summary="Bootstrap user profile",
    description=DOC_AUTH_BOOTSTRAP,
    response_model=BootstrapResponse,
    responses={401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_auth_bootstrap(
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer token (Firebase ID token or web JWT).",
    ),
) -> BootstrapResponse:
    user_id = resolve_user_id(authorization=authorization)
    email: str | None = None
    display_name: str | None = None
    token = _bearer_token(authorization)
    if token:
        user = verify_access_token(token)
        email, display_name = profile_from_auth_user(user)
    result = await asyncio.to_thread(
        bootstrap_user_profile,
        user_id,
        email=email,
        display_name=display_name,
    )
    return BootstrapResponse.model_validate(result)


def _user_id_from_chat(
    body: ChatRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


def _user_id_from_reset(
    body: ResetRequest,
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback(body.user_id, authorization)


@app.post(
    "/api/chat",
    tags=["chat"],
    summary="Calendar AI chat",
    description=DOC_CHAT,
    response_model=ChatResponse,
    responses={
        401: {"model": ErrorDetail},
        404: {"model": ErrorDetail},
    },
    openapi_extra={"security": [{"BearerAuth": []}]},
)
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


@app.get(
    "/api/state",
    tags=["system"],
    summary="Debug state (optional)",
    description=DOC_STATE,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_state(
    light: bool = Query(True, description="When true, omit heavy debug payloads."),
    user_id: str = Query("default", description="Ignored when Bearer token present."),
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer token (Firebase or local JWT).",
    ),
):
    uid = require_user_with_fallback(user_id, authorization)
    return await asyncio.to_thread(get_debug_state, uid, light)


@app.post(
    "/api/reset",
    tags=["system"],
    summary="Reset calendar + chat (dev)",
    description=DOC_RESET,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_reset(
    body: ResetRequest,
    user_id: str = Depends(_user_id_from_reset),
):
    return await asyncio.to_thread(reset_user_to_seed, user_id)


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="Missing todai/api/static/index.html")
    return FileResponse(index_path)


@app.get("/goal-debug")
def goal_debug_ui():
    """Separate debug UI for goal routes, prompts, and execution traces."""
    debug_path = STATIC_DIR / "goal-debug" / "index.html"
    if not debug_path.exists():
        raise HTTPException(status_code=500, detail="Missing todai/api/static/goal-debug/index.html")
    return FileResponse(debug_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("TODAI_HOST", "0.0.0.0")
    port = server_port()
    print(f"TodAI — http://127.0.0.1:{port}/", flush=True)
    uvicorn.run(
        "todai.api.main:app",
        host=host,
        port=port,
        reload=os.environ.get("TODAI_RELOAD", "").strip().lower() in ("1", "true", "yes"),
        log_level=os.environ.get("TODAI_LOG_LEVEL", "info").lower(),
    )
