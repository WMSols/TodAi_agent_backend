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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from todai.api.service import confirm, get_debug_state, process_chat, reject
from todai.database.storage import (
    DATA_DIR,
    ChatRequest,
    ConfirmRequest,
    RejectRequest,
    ResetRequest,
    log_api_response,
    logger,
    planner_mode,
    reset_user_to_seed,
    setup_logging,
)

setup_logging()
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="TodAI", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "planner": planner_mode(), "data_dir": str(DATA_DIR)}


@app.post("/api/chat")
async def api_chat(body: ChatRequest):
    try:
        resp = await asyncio.to_thread(process_chat, body.user_id, body.message)
        log_api_response("chat", user_id=body.user_id, resp=resp, user_message=body.message)
        return resp
    except FileNotFoundError as e:
        logger.error("chat 404 user=%s: %s", body.user_id, e)
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception:
        logger.exception("chat failed user=%s", body.user_id)
        raise


@app.post("/api/confirm")
async def api_confirm(body: ConfirmRequest):
    resp = await asyncio.to_thread(confirm, body.user_id)
    log_api_response("confirm", user_id=body.user_id, resp=resp)
    return resp


@app.post("/api/reject")
async def api_reject(body: RejectRequest):
    resp = await asyncio.to_thread(reject, body.user_id)
    log_api_response("reject", user_id=body.user_id, resp=resp)
    return resp


@app.get("/api/state")
async def api_state(user_id: str = "default", light: bool = True):
    return await asyncio.to_thread(get_debug_state, user_id, light)


@app.post("/api/reset")
async def api_reset(body: ResetRequest):
    return await asyncio.to_thread(reset_user_to_seed, DATA_DIR, body.user_id)


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="Missing todai/api/static/index.html")
    return FileResponse(index_path)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
        log_level=os.environ.get("TODAI_LOG_LEVEL", "debug").lower(),
    )
