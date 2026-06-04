"""HTTP routes for editing goal tasks from the My events calendar UI."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from todai.api.auth import require_user_with_fallback
from todai.api.logging import logger
from todai.api.openapi_docs import DOC_GOAL_TASK_DELETE, DOC_GOAL_TASK_PATCH
from todai.api.schemas import ErrorDetail, GoalTaskDeleteResponse, GoalTaskPatchResponse
from todai.goal_planner.session_store import GoalPlanSessionStore

router = APIRouter(prefix="/api/goals/tasks", tags=["goal-tasks"])


class GoalTaskUpdate(BaseModel):
    """PATCH body — only include fields you want to change."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "title": "Morning run",
        "task_date": "2026-06-03",
        "start_time": "07:00",
        "end_time": "07:30",
        "status": "done",
    }})

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    task_date: str | None = Field(None, description="YYYY-MM-DD")
    start_time: str | None = Field(None, description="HH:MM or HH:MM:SS")
    end_time: str | None = Field(None, description="HH:MM or HH:MM:SS")
    status: str | None = Field(None, max_length=40)


def _user_id(
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer token (Firebase or local JWT).",
    ),
) -> str:
    return require_user_with_fallback("default", authorization)


@router.patch(
    "/{task_id}",
    summary="Update goal task",
    description=DOC_GOAL_TASK_PATCH,
    response_model=GoalTaskPatchResponse,
    responses={404: {"model": ErrorDetail}, 400: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_update_goal_task(
    body: GoalTaskUpdate,
    task_id: str = Path(..., description="Goal task UUID from GET /api/calendar/agenda"),
    user_id: str = Depends(_user_id),
) -> GoalTaskPatchResponse:
    try:
        store = GoalPlanSessionStore(user_id)
        row = await asyncio.to_thread(
            store.update_goal_task,
            task_id,
            **body.model_dump(exclude_unset=True),
        )
        return GoalTaskPatchResponse(ok=True, task=row)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("goal task update failed user=%s task=%s", user_id, task_id)
        raise


@router.delete(
    "/{task_id}",
    summary="Delete goal task",
    description=DOC_GOAL_TASK_DELETE,
    response_model=GoalTaskDeleteResponse,
    responses={404: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_delete_goal_task(
    task_id: str = Path(..., description="Goal task UUID"),
    user_id: str = Depends(_user_id),
) -> GoalTaskDeleteResponse:
    try:
        store = GoalPlanSessionStore(user_id)
        result = await asyncio.to_thread(store.delete_goal_task, task_id)
        return GoalTaskDeleteResponse.model_validate(result)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception:
        logger.exception("goal task delete failed user=%s task=%s", user_id, task_id)
        raise
