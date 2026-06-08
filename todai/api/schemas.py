"""Pydantic models for Swagger / OpenAPI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthUserOut(BaseModel):
    """Authenticated user identity returned after login/register."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "login_name": "alikhan",
        "display_name": "Ali Khan",
        "email": "ali@example.com",
    }})

    id: str = Field(..., description="User id — use as `user_id` in chat body when needed; identity comes from Bearer token.")
    login_name: str | None = Field(None, description="Normalized username (local accounts only).")
    display_name: str | None = Field(None, description="Human-readable name.")
    email: str | None = Field(None, description="Email if provided at registration or from Firebase.")


class AuthTokenResponse(BaseModel):
    """Returned by POST /api/auth/login and POST /api/auth/register (web only)."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "token_type": "bearer",
        "expires_in": 604800,
        "user": {
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "login_name": "alikhan",
            "display_name": "Ali Khan",
            "email": "ali@example.com",
        },
    }})

    access_token: str = Field(
        ...,
        description="JWT for web clients. Send on every request: `Authorization: Bearer <access_token>`.",
    )
    token_type: str = Field("bearer", description="Always `bearer`.")
    expires_in: int = Field(..., description="Token lifetime in seconds (default 7 days = 604800).")
    user: AuthUserOut


class AuthProvidersOut(BaseModel):
    firebase: bool = Field(..., description="Firebase JWT enabled.")
    local: bool = Field(..., description="Web username/password login enabled.")


class AuthConfigResponse(BaseModel):
    """GET /api/auth/config — server auth flags for app startup."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "auth_required": True,
        "auth_dev_allow_default": False,
        "storage": "supabase",
        "providers": {"firebase": True, "local": True},
        "firebase_project_id": "your-firebase-project",
    }})

    auth_required: bool = Field(..., description="If true, send `Authorization: Bearer <token>` on protected routes.")
    auth_dev_allow_default: bool = Field(..., description="If true, missing token uses dev user `default` (server only).")
    storage: str = Field("supabase", description="Database backend label.")
    providers: AuthProvidersOut
    firebase_project_id: str | None = Field(None, description="Firebase project id for the app.")


class BootstrapResponse(BaseModel):
    """POST /api/auth/bootstrap — creates profile + seed calendar on first login."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "ok": True,
        "user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "display_name": "Ali Khan",
        "email": "ali@example.com",
        "storage": "supabase",
    }})

    ok: bool = Field(True, description="Always true on success")
    user_id: str = Field(..., description="Stable user id for this account")
    display_name: str = Field(..., description="Profile display name")
    email: str | None = Field(None, description="Email from Firebase or registration")
    storage: str = Field(..., description="Storage backend used for profile")


class HealthResponse(BaseModel):
    """GET /health — liveness and server configuration flags."""

    ok: bool = True
    planner: str = Field(..., description="AI planner backend label")
    storage: str = Field(..., description="Storage backend, e.g. supabase")
    supabase_configured: bool
    firebase_configured: bool = Field(..., description="Firebase JWT auth enabled")
    local_auth_configured: bool = Field(..., description="True when web username/password login is enabled")
    auth_required: bool = Field(..., description="True when clients must send Bearer token")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class CalendarEventOut(BaseModel):
    id: str
    title: str
    description: str = ""
    start: str = Field(..., description="Local naive ISO datetime, e.g. 2026-06-03T09:00:00")
    end: str = Field(..., description="Local naive ISO datetime")
    kind: str = "personal"
    location: str = ""
    all_day: bool = False
    source: str = "user"
    recurrence_id: str | None = None
    recurrence: dict[str, Any] | None = None


class GoalTaskOut(BaseModel):
    id: str
    title: str
    description: str = ""
    task_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field("", description="HH:MM:SS or empty for flexible")
    end_time: str = Field("", description="HH:MM:SS or empty")
    status: str = Field("pending", description="pending | done | skipped")
    plan_id: str | None = None
    goal_id: str | None = None
    kind: str = "goal_task"


class CalendarAgendaResponse(BaseModel):
    """GET /api/calendar/agenda — schedule events + goal tasks for calendar UI."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"example": {
        "from": "2026-06-01",
        "to": "2026-06-30",
        "timezone": "Asia/Karachi",
        "events": [{
            "id": "evt-uuid",
            "title": "Team meeting",
            "description": "",
            "start": "2026-06-03T09:00:00",
            "end": "2026-06-03T10:00:00",
            "kind": "personal",
            "location": "",
            "all_day": False,
            "source": "user",
            "recurrence_id": None,
            "recurrence": None,
        }],
        "goal_tasks": [{
            "id": "task-uuid",
            "title": "Morning run",
            "description": "30 min easy pace",
            "task_date": "2026-06-03",
            "start_time": "07:00:00",
            "end_time": "07:30:00",
            "status": "pending",
            "plan_id": "plan-uuid",
            "goal_id": "goal-uuid",
            "kind": "goal_task",
        }],
    }})

    from_: str = Field(..., alias="from", description="Range start YYYY-MM-DD")
    to: str = Field(..., description="Range end YYYY-MM-DD")
    timezone: str = Field(..., description="User timezone for displaying times")
    events: list[CalendarEventOut] = Field(..., description="User schedule blocks (purple in UI)")
    goal_tasks: list[GoalTaskOut] = Field(..., description="Goal plan tasks (green in UI)")


class CalendarEventsListResponse(BaseModel):
    """GET /api/calendar/events — schedule events in range."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(..., alias="from", description="Echo of query `from`")
    to: str = Field(..., description="Echo of query `to`")
    timezone: str
    events: list[CalendarEventOut]


class CalendarEventCreateResponse(BaseModel):
    """POST /api/calendar/events — one or more created events."""

    ok: bool = True
    events: list[CalendarEventOut]
    recurrence: dict[str, Any] | None = None
    schedule_version: int


class CalendarEventUpdateResponse(BaseModel):
    """PATCH /api/calendar/events/{event_id}."""

    ok: bool = True
    event: CalendarEventOut
    schedule_version: int


class CalendarEventDeleteResponse(BaseModel):
    """DELETE /api/calendar/events/{event_id}."""

    ok: bool = True
    deleted: int = Field(..., description="Number of rows removed (1 or whole series count)")
    schedule_version: int


class _GoalPlanResponseBase(BaseModel):
    """Base goal planner JSON. Extra fields may appear (dev traces)."""

    model_config = ConfigDict(extra="allow")

    plan_id: str | None = Field(None, description="7-day plan id — use in /message and GET /{plan_id}")
    goal_id: str | None = Field(None, description="Parent goal id — not the same as plan_id")
    reply_text: str | None = Field(None, description="AI text to show the user")
    assistant_text: str | None = Field(None, description="Same as reply_text")
    phase: str | None = Field(None, description="Plan step: interrogate, active, list, etc.")
    state: str = Field("idle", description="Usually idle")
    agent_mode: str | None = Field("goal_plan", description="Always goal_plan for this API group")
    schedule_display: dict[str, Any] | None = Field(
        None, description="Optional week calendar JSON for the UI",
    )
    plans: list[dict[str, Any]] | None = Field(
        None, description="Only for GET /plans — list of week plans",
    )
    goals: list[dict[str, Any]] | None = Field(
        None, description="Only for GET /plans — list of goals",
    )
    messages: list[dict[str, Any]] | None = Field(
        None, description="Only for GET /{plan_id} — chat history",
    )
    session: dict[str, Any] | None = Field(
        None, description="Only for GET /{plan_id} — plan session state",
    )
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    api_usage: dict[str, Any] | None = None
    debug: dict[str, Any] | None = None


class GoalPlanStartResponse(_GoalPlanResponseBase):
    """POST /api/goals/plan/start — new plan."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "plan_id": "cd581499-d826-4452-8563-52590eca125b",
                "goal_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "reply_text": "What time of day works best for training?",
                "phase": "interrogate",
                "state": "idle",
                "start_date": "2026-06-04",
                "end_date": "2026-06-10",
            }
        },
    )


class GoalPlanMessageResponse(_GoalPlanResponseBase):
    """POST /api/goals/plan/message — goal chat reply."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "plan_id": "cd581499-d826-4452-8563-52590eca125b",
                "reply_text": "Here are your tasks for this week…",
                "phase": "active",
                "state": "idle",
                "schedule_display": {"schema": "todai.schedule.v1", "days": []},
            }
        },
    )


class GoalPlanListResponse(_GoalPlanResponseBase):
    """GET /api/goals/plan/plans — list goals and plans (not a chat reply)."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "phase": "list",
                "plans": [
                    {
                        "id": "cd581499-d826-4452-8563-52590eca125b",
                        "goal_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "goal_title": "Get fit",
                        "start_date": "2026-06-04",
                        "end_date": "2026-06-10",
                        "progress": {"total": 21, "done": 1, "pending": 20, "percent": 4},
                    }
                ],
                "goals": [{"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "title": "Get fit"}],
            }
        },
    )


class GoalPlanGetResponse(_GoalPlanResponseBase):
    """GET /api/goals/plan/{plan_id} — one plan + history."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "plan_id": "cd581499-d826-4452-8563-52590eca125b",
                "session": {"phase": "active"},
                "messages": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}],
                "schedule_display": {"schema": "todai.schedule.v1", "days": []},
            }
        },
    )


# Back-compat alias for code that still references GoalPlanApiResponse
GoalPlanApiResponse = _GoalPlanResponseBase


class GoalTaskPatchResponse(BaseModel):
    ok: bool = True
    task: dict[str, Any]


class GoalTaskDeleteResponse(BaseModel):
    ok: bool = True
    deleted: str


class ErrorDetail(BaseModel):
    detail: str
