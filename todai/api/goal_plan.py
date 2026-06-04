"""HTTP routes for goal planning (separate from /api/chat)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from todai.api.auth import require_user_with_fallback
from todai.api.logging import log_api_response, logger
from todai.api.openapi_docs import (
    DOC_GOAL_GET,
    DOC_GOAL_LIST,
    DOC_GOAL_MESSAGE,
    DOC_GOAL_START,
)
from todai.api.schemas import ErrorDetail, GoalPlanApiResponse
from todai.goal_planner.service import (
    get_goal_plan_state,
    list_goal_plans,
    process_goal_plan_message,
    start_goal_plan,
)

router = APIRouter(prefix="/api/goals/plan", tags=["goal-plan"])


class GoalPlanStartRequest(BaseModel):
    """Start a new 7-day goal plan. Provide what the user wants to achieve."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "achievement": "Run 5km daily and stretch every morning this week",
        "title": "",
        "description": "",
    }})

    achievement: str = Field("", max_length=4000, description="Main user goal text (required unless title/description set).")
    title: str = Field("", max_length=200)
    description: str = Field("", max_length=4000)


class GoalPlanMessageRequest(BaseModel):
    plan_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=4000)
    ui_mode: str = Field(
        "my_goals",
        description="my_goals = conversational; new_goal = static 4-question intake only",
    )


def _user_id(
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer token (Firebase or local JWT).",
    ),
) -> str:
    return require_user_with_fallback("default", authorization)


@router.post(
    "/start",
    summary="Start goal plan",
    description=DOC_GOAL_START,
    response_model=GoalPlanApiResponse,
    responses={422: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_goal_plan_start(
    body: GoalPlanStartRequest,
    user_id: str = Depends(_user_id),
) -> GoalPlanApiResponse:
    achievement = (
        (body.achievement or "").strip()
        or (body.description or "").strip()
        or (body.title or "").strip()
    )
    if not achievement:
        raise HTTPException(
            status_code=422,
            detail="achievement is required (what you want to achieve)",
        )
    try:
        resp = await asyncio.to_thread(
            start_goal_plan,
            user_id,
            achievement=achievement,
            title=body.title,
            description=body.description,
        )
        log_api_response(
            "goal-plan/start",
            user_id=user_id,
            resp=resp,
            user_message=achievement,
        )
        return GoalPlanApiResponse.model_validate(resp)
    except Exception:
        logger.exception("goal-plan/start failed user=%s", user_id)
        raise


@router.post(
    "/message",
    summary="Goal planner chat",
    description=DOC_GOAL_MESSAGE,
    response_model=GoalPlanApiResponse,
    responses={401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_goal_plan_message(
    body: GoalPlanMessageRequest,
    user_id: str = Depends(_user_id),
) -> GoalPlanApiResponse:
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
        return GoalPlanApiResponse.model_validate(resp)
    except Exception:
        logger.exception("goal-plan/message failed user=%s plan=%s", user_id, body.plan_id)
        raise


@router.get(
    "/plans",
    summary="List goal plans",
    description=DOC_GOAL_LIST,
    response_model=GoalPlanApiResponse,
    responses={401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_goal_plans_list(
    user_id: str = Depends(_user_id),
) -> GoalPlanApiResponse:
    result = await asyncio.to_thread(list_goal_plans, user_id)
    return GoalPlanApiResponse.model_validate(result)


@router.get(
    "/{plan_id}",
    summary="Get goal plan state",
    description=DOC_GOAL_GET,
    response_model=GoalPlanApiResponse,
    responses={401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_goal_plan_get(
    plan_id: str = Path(..., description="Plan UUID from GET /plans or POST /start"),
    include_messages: bool = Query(True, description="Include chat history when true"),
    user_id: str = Depends(_user_id),
) -> GoalPlanApiResponse:
    result = await asyncio.to_thread(
        get_goal_plan_state,
        user_id,
        plan_id,
        include_messages=include_messages,
    )
    return GoalPlanApiResponse.model_validate(result)
