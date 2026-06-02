"""FastAPI request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = "default"
    message: str = Field(..., min_length=1, max_length=8000)


class ConfirmRequest(BaseModel):
    user_id: str = "default"


class RejectRequest(BaseModel):
    user_id: str = "default"


class ResetRequest(BaseModel):
    user_id: str = "default"


class RegisterRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=256)


class ChatResponse(BaseModel):
    assistant_text: str
    state: str
    schedule_version: int
    pending_proposal_id: str | None = None
    agent_mode: str | None = None
    reply_text: str | None = None
    suggested_action: str | None = None
    agent_state: str | None = None
    schedule_display: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    validator_errors: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    api_usage: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.reply_text is None:
            object.__setattr__(self, "reply_text", self.assistant_text)
        if self.agent_state is None:
            object.__setattr__(self, "agent_state", self.agent_mode)
