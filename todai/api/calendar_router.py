"""HTTP routes for calendar events CRUD (no agent)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from todai.api.auth import require_user_with_fallback
from todai.api.logging import logger
from todai.api.openapi_docs import (
    DOC_CAL_AGENDA,
    DOC_CAL_CREATE,
    DOC_CAL_DELETE,
    DOC_CAL_LIST_EVENTS,
    DOC_CAL_UPDATE,
)
from todai.api.schemas import (
    CalendarAgendaResponse,
    CalendarEventCreateResponse,
    CalendarEventDeleteResponse,
    CalendarEventUpdateResponse,
    CalendarEventsListResponse,
    ErrorDetail,
)
from todai.calendar_api import service as cal_svc

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class RecurrenceInput(BaseModel):
    enabled: bool = False
    weekly_mode: str = Field("same_day", description="same_day | weekdays")
    skip_days: list[int] = Field(default_factory=list, description="0=Mon .. 6=Sun to skip")
    repeat_weeks: int = Field(12, ge=1, le=52)


class CalendarEventCreate(BaseModel):
    """Create a schedule block. Times are local naive ISO (user timezone applied server-side)."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "title": "Team meeting",
        "description": "Weekly sync",
        "start": "2026-06-03T09:00:00",
        "end": "2026-06-03T10:00:00",
        "kind": "personal",
    }})

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    start: str = Field(..., description="Local naive ISO e.g. 2026-06-03T09:00:00")
    end: str = Field(...)
    kind: str = Field("personal", max_length=40)
    location: str = Field("", max_length=200)
    all_day: bool = False
    recurrence: RecurrenceInput | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    start: str | None = None
    end: str | None = None
    kind: str | None = Field(None, max_length=40)
    location: str | None = Field(None, max_length=200)
    all_day: bool | None = None


def _user_id(
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer token (Firebase or web JWT).",
    ),
) -> str:
    return require_user_with_fallback("default", authorization)


@router.get(
    "/events",
    summary="List schedule events (date range)",
    description=DOC_CAL_LIST_EVENTS,
    response_model=CalendarEventsListResponse,
    responses={401: {"model": ErrorDetail}, 400: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_list_events(
    from_date: str = Query(..., alias="from", description="Start date inclusive, YYYY-MM-DD"),
    to_date: str = Query(..., alias="to", description="End date inclusive, YYYY-MM-DD"),
    user_id: str = Depends(_user_id),
) -> CalendarEventsListResponse:
    try:
        return CalendarEventsListResponse.model_validate(
            await asyncio.to_thread(cal_svc.list_events, user_id, date_from=from_date, date_to=to_date)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("calendar list failed user=%s", user_id)
        raise


@router.get(
    "/agenda",
    summary="Month calendar grid (events + goal tasks)",
    description=DOC_CAL_AGENDA,
    response_model=CalendarAgendaResponse,
    responses={401: {"model": ErrorDetail}, 400: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_list_agenda(
    from_date: str = Query(..., alias="from", description="Range start (YYYY-MM-DD, inclusive)"),
    to_date: str = Query(..., alias="to", description="Range end (YYYY-MM-DD, inclusive)"),
    user_id: str = Depends(_user_id),
) -> CalendarAgendaResponse:
    try:
        payload = await asyncio.to_thread(cal_svc.list_agenda, user_id, date_from=from_date, date_to=to_date)
        return CalendarAgendaResponse.model_validate(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("calendar agenda failed user=%s", user_id)
        raise


@router.post(
    "/events",
    summary="Create schedule event",
    description=DOC_CAL_CREATE,
    response_model=CalendarEventCreateResponse,
    responses={400: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_create_event(
    body: CalendarEventCreate,
    user_id: str = Depends(_user_id),
) -> CalendarEventCreateResponse:
    try:
        payload = body.model_dump()
        if body.recurrence:
            payload["recurrence"] = body.recurrence.model_dump()
        result = await asyncio.to_thread(cal_svc.create_event, user_id, payload)
        return CalendarEventCreateResponse.model_validate(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("calendar create failed user=%s", user_id)
        raise


@router.patch(
    "/events/{event_id}",
    summary="Update schedule event",
    description=DOC_CAL_UPDATE,
    response_model=CalendarEventUpdateResponse,
    responses={404: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_update_event(
    body: CalendarEventUpdate,
    event_id: str = Path(..., description="Event UUID from agenda or GET /events"),
    user_id: str = Depends(_user_id),
) -> CalendarEventUpdateResponse:
    try:
        patch = body.model_dump(exclude_unset=True)
        result = await asyncio.to_thread(cal_svc.update_event, user_id, event_id, patch)
        return CalendarEventUpdateResponse.model_validate(result)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception:
        logger.exception("calendar update failed user=%s event=%s", user_id, event_id)
        raise


@router.delete(
    "/events/{event_id}",
    summary="Delete schedule event",
    description=DOC_CAL_DELETE,
    response_model=CalendarEventDeleteResponse,
    responses={404: {"model": ErrorDetail}, 401: {"model": ErrorDetail}},
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def api_delete_event(
    event_id: str = Path(..., description="Event UUID"),
    delete_series: bool = Query(False, description="true = delete entire recurrence series"),
    user_id: str = Depends(_user_id),
) -> CalendarEventDeleteResponse:
    try:
        result = await asyncio.to_thread(
            cal_svc.delete_event, user_id, event_id, delete_series=delete_series
        )
        return CalendarEventDeleteResponse.model_validate(result)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception:
        logger.exception("calendar delete failed user=%s event=%s", user_id, event_id)
        raise
