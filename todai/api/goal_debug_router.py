"""Debug API for goal planner routes, prompts, and execution traces."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from pydantic import BaseModel, Field

from todai.api.auth import require_user_with_fallback
from todai.goal_planner.debug.catalog import get_goal_catalog, get_prompt_entry
from todai.goal_planner.debug.prompt_overrides import (
    clear_all_overrides,
    clear_override,
    get_effective_prompt,
    list_overrides,
    set_override,
)
from todai.goal_planner.service import get_goal_debug_history

router = APIRouter(prefix="/api/goals/debug", tags=["goal-debug"])


class PromptOverrideBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000)


def _user_id(
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return require_user_with_fallback("default", authorization)


@router.get("/catalog")
async def api_goal_debug_catalog() -> dict:
    """Routes, prompts, architecture pattern, and API links for the debug UI."""
    return await asyncio.to_thread(get_goal_catalog)


@router.get("/prompts")
async def api_goal_debug_prompts() -> dict:
    """All goal prompts with default text and active runtime overrides."""
    catalog = await asyncio.to_thread(get_goal_catalog)
    overrides = await asyncio.to_thread(list_overrides)
    prompts = []
    for entry in catalog["prompts"]:
        pid = entry["id"]
        effective = await asyncio.to_thread(get_effective_prompt, pid)
        prompts.append({**entry, **effective})
    return {"prompts": prompts, "overrides": overrides}


@router.get("/prompts/{prompt_id}")
async def api_goal_debug_prompt(prompt_id: str = Path(..., min_length=1)) -> dict:
    try:
        entry = await asyncio.to_thread(get_prompt_entry, prompt_id)
        effective = await asyncio.to_thread(get_effective_prompt, prompt_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {**entry, **effective}


@router.put("/prompts/{prompt_id}")
async def api_goal_debug_set_prompt(
    body: PromptOverrideBody,
    prompt_id: str = Path(..., min_length=1),
) -> dict:
    try:
        result = await asyncio.to_thread(set_override, prompt_id, body.content)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "prompt": result}


@router.delete("/prompts/{prompt_id}")
async def api_goal_debug_clear_prompt(prompt_id: str = Path(..., min_length=1)) -> dict:
    cleared = await asyncio.to_thread(clear_override, prompt_id)
    if not cleared:
        raise HTTPException(status_code=404, detail="No override for this prompt")
    effective = await asyncio.to_thread(get_effective_prompt, prompt_id)
    return {"ok": True, "prompt": effective}


@router.post("/prompts/reset")
async def api_goal_debug_reset_prompts() -> dict:
    count = await asyncio.to_thread(clear_all_overrides)
    return {"ok": True, "cleared": count}


@router.get("/plans/{plan_id}/history")
async def api_goal_debug_history(
    plan_id: str = Path(..., min_length=1),
    user_id: str = Depends(_user_id),
) -> dict:
    return await asyncio.to_thread(get_goal_debug_history, user_id, plan_id)
