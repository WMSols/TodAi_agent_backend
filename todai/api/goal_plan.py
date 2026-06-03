"""HTTP routes for goal planning (separate from /api/chat)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from todai.api.auth import require_user_with_fallback
from todai.api.logging import log_api_response, logger
from todai.database.config import use_local_storage
from todai.goal_planner.service import (
    get_goal_plan_state,
    list_goal_plans,
    process_goal_plan_message,
    start_goal_plan,
)

router = APIRouter(prefix="/api/goals/plan", tags=["goal-plan"])


class GoalPlanStartRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=4000)


class GoalPlanMessageRequest(BaseModel):
    plan_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=4000)
    ui_mode: str = Field(
        "my_goals",
        description="my_goals = conversational; new_goal = static 4-question intake only",
    )


def _user_id(authorization: str | None = Header(None, alias="Authorization")) -> str:
    return require_user_with_fallback("default", authorization)


@router.post("/start")
async def api_goal_plan_start(
    body: GoalPlanStartRequest,
    user_id: str = Depends(_user_id),
) -> dict[str, Any]:
    if use_local_storage():
        raise HTTPException(
            status_code=400,
            detail="Goal planner requires LOCAL=false and Supabase. Run docs/supabase/001_message_buckets.sql.",
        )
    try:
        resp = await asyncio.to_thread(
            start_goal_plan,
            user_id,
            title=body.title,
            description=body.description,
        )
        log_api_response(
            "goal-plan/start",
            user_id=user_id,
            resp=resp,
            user_message=body.title,
        )
        return resp
    except Exception:
        logger.exception("goal-plan/start failed user=%s", user_id)
        raise


@router.post("/message")
async def api_goal_plan_message(
    body: GoalPlanMessageRequest,
    user_id: str = Depends(_user_id),
) -> dict[str, Any]:
    if use_local_storage():
        raise HTTPException(status_code=400, detail="Goal planner requires Supabase (LOCAL=false).")
    try:
        ui_mode = body.ui_mode if body.ui_mode in ("my_goals", "new_goal") else "my_goals"
        resp = await asyncio.to_thread(
            process_goal_plan_message,
            user_id,
            body.plan_id,
            body.message,
            ui_mode=ui_mode,
        )
        log_api_response(
            "goal-plan/message",
            user_id=user_id,
            resp=resp,
            user_message=body.message,
        )
        return resp
    except Exception:
        logger.exception("goal-plan/message failed user=%s plan=%s", user_id, body.plan_id)
        raise


@router.get("/plans")
async def api_goal_plans_list(
    user_id: str = Depends(_user_id),
) -> dict[str, Any]:
    if use_local_storage():
        raise HTTPException(status_code=400, detail="Goal planner requires Supabase (LOCAL=false).")
    return await asyncio.to_thread(list_goal_plans, user_id)


@router.get("/{plan_id}")
async def api_goal_plan_get(
    plan_id: str,
    include_messages: bool = True,
    user_id: str = Depends(_user_id),
) -> dict[str, Any]:
    if use_local_storage():
        raise HTTPException(status_code=400, detail="Goal planner requires Supabase (LOCAL=false).")
    return await asyncio.to_thread(
        get_goal_plan_state,
        user_id,
        plan_id,
        include_messages=include_messages,
    )
