"""FastAPI request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """POST /api/chat — calendar AI (not goals)."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "message": "Show my schedule for next week",
    }})

    user_id: str = Field(
        "default",
        description="Ignored when Bearer token is sent.",
    )
    message: str = Field(..., min_length=1, max_length=8000, description="What the user typed.")


class ResetRequest(BaseModel):
    user_id: str = Field(
        "default",
        description="Ignored when Bearer token present. Resets calendar to seed data and clears chat.",
    )


class RegisterRequest(BaseModel):
    """Web-only registration."""

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
    """Web-only login."""

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
    """POST /api/chat response."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "assistant_text": "Here is your week…",
        "state": "idle",
        "schedule_version": 3,
        "schedule_display": {"schema": "todai.schedule.v1", "days": []},
    }})

    assistant_text: str = Field(..., description="**Show this** to the user.")
    state: str = Field(..., description="idle | analyzing | requesting_data | error")
    schedule_version: int = Field(..., description="Bigger number = calendar changed, refresh UI")
    pending_proposal_id: str | None = Field(None, description="User must confirm a change when set")
    agent_mode: str | None = None
    reply_text: str | None = Field(None, description="Same as assistant_text")
    suggested_action: str | None = None
    agent_state: str | None = None
    schedule_display: dict[str, Any] | None = Field(
        None,
        description="Optional JSON to draw calendar in the app",
    )
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    validator_errors: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    api_usage: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.reply_text is None:
            object.__setattr__(self, "reply_text", self.assistant_text)
        if self.agent_state is None:
            object.__setattr__(self, "agent_state", self.agent_mode)
