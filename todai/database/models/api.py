"""FastAPI request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Send a message to the calendar AI agent."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "user_id": "default",
        "message": "Show my schedule for next week",
    }})

    user_id: str = Field(
        "default",
        description="Ignored when Authorization Bearer token is present. Use `default` only in dev sandbox mode.",
    )
    message: str = Field(..., min_length=1, max_length=8000, description="User message in natural language.")


class ResetRequest(BaseModel):
    user_id: str = Field(
        "default",
        description="Ignored when Bearer token present. Resets calendar to seed data and clears chat.",
    )


class RegisterRequest(BaseModel):
    """Web-only account creation. Flutter users register via Firebase, not this endpoint."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "display_name": "Ali Khan",
        "email": "ali@example.com",
        "password": "your-secure-password",
    }})

    display_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Your name — also used as username (normalized to letters/numbers only).",
    )
    email: str = Field(
        "",
        max_length=320,
        description="Optional email — can be used to sign in instead of username.",
    )
    password: str = Field(..., min_length=1, max_length=256, description="Account password.")


class LoginRequest(BaseModel):
    """Web-only login. Flutter uses Firebase ID token instead."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "username": "alikhan",
        "password": "your-secure-password",
    }})

    username: str = Field(
        ...,
        min_length=1,
        max_length=320,
        description="Login name (from registration) or email address.",
    )
    password: str = Field(..., min_length=1, max_length=256)


class ChatResponse(BaseModel):
    """AI agent reply — includes optional structured schedule for UI rendering."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "assistant_text": "Here is your week…",
        "state": "idle",
        "schedule_version": 3,
        "schedule_display": {"schema": "todai.schedule.v1", "days": []},
    }})

    assistant_text: str = Field(..., description="Plain-text reply shown to the user.")
    state: str = Field(..., description="Agent FSM state: idle | analyzing | requesting_data | error")
    schedule_version: int = Field(..., description="Increments when calendar data changes.")
    pending_proposal_id: str | None = Field(None, description="Set when agent awaits confirm/cancel.")
    agent_mode: str | None = None
    reply_text: str | None = Field(None, description="Same as assistant_text (legacy alias).")
    suggested_action: str | None = None
    agent_state: str | None = None
    schedule_display: dict[str, Any] | None = Field(
        None,
        description="Structured calendar JSON (`todai.schedule.v1`) for rich UI — may include goal tasks.",
    )
    tool_trace: list[dict[str, Any]] = Field(default_factory=list, description="Debug: tools invoked this turn.")
    validator_errors: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    api_usage: dict[str, Any] | None = Field(None, description="Groq usage snapshot for this turn.")

    def model_post_init(self, __context: Any) -> None:
        if self.reply_text is None:
            object.__setattr__(self, "reply_text", self.assistant_text)
        if self.agent_state is None:
            object.__setattr__(self, "agent_state", self.agent_mode)
