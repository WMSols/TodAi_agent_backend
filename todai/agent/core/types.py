"""
types.py — shared types for one orchestrated turn

  AgentRoute     — router intent label (chat, schedule_preview, …)
  AgentMode      — UI-facing mode string
  ConversationState — chat.json FSM values
  TurnContext    — everything passed into an intent handler
  IntentResult   — reply + ops + display returned by a handler
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from todai.agent.planner.llm import AgentRoute
from todai.agent.routing.preview_range import PreviewRange
from todai.database.models import ChatResponse
from todai.database.stores import UserStore


class ConversationState(str, Enum):
    IDLE = "idle"
    REQUESTING_DATA = "requesting_data"
    ANALYZING = "analyzing"
    ERROR = "error"


class AgentMode(str, Enum):
    CHAT = "chat"
    SCHEDULE_QA = "schedule_qa"
    SCHEDULE_WRITE = "schedule_write"


def route_to_agent_mode(route: AgentRoute) -> AgentMode:
    return {
        AgentRoute.CHAT: AgentMode.CHAT,
        AgentRoute.SCHEDULE_PREVIEW: AgentMode.SCHEDULE_QA,
        AgentRoute.SCHEDULE_WRITE: AgentMode.SCHEDULE_WRITE,
        AgentRoute.SCHEDULE_DELETE: AgentMode.SCHEDULE_WRITE,
    }[route]


@dataclass
class TurnContext:
    store: UserStore
    user_id: str
    message: str
    chat: dict[str, Any]
    history: list[dict[str, str]]
    route: AgentRoute
    server_snapshot: dict[str, Any]
    conversation: dict[str, Any]
    full_index: dict[str, Any]
    read_results: list[dict[str, Any]] = field(default_factory=list)
    date_anchor: dict[str, Any] | None = None
    highlights: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    router_dbg: dict[str, Any] | None = None
    preview_range: PreviewRange | None = None


@dataclass
class IntentResult:
    reply_text: str
    operations: list[dict[str, Any]] = field(default_factory=list)
    schedule_display: dict[str, Any] | None = None
    specialist_dbg: dict[str, Any] | None = None
    apply_errors: list[dict[str, Any]] = field(default_factory=list)
    months_written: int = 0


def chat_response_from_turn(
    ctx: TurnContext,
    result: IntentResult,
    *,
    mode: AgentMode,
    user_id: str,
) -> ChatResponse:
    from todai.api.middleware.rate_limit import groq_tracker
    from todai.agent.planner.groq_config import planner_mode

    usage = groq_tracker.usage_snapshot(user_id)
    debug: dict[str, Any] = {
        "pipeline": "orchestrator",
        "route": ctx.route.value,
        "intent": ctx.route.value,
        "planner": planner_mode(),
        "api_usage": usage,
    }
    if ctx.router_dbg:
        debug["router_groq"] = ctx.router_dbg
    if result.specialist_dbg:
        debug["specialist_groq"] = result.specialist_dbg

    return ChatResponse(
        assistant_text=result.reply_text,
        reply_text=result.reply_text,
        state=ctx.chat.get("state", ConversationState.IDLE.value),
        schedule_version=int(ctx.chat.get("schedule_version", 1)),
        agent_mode=mode.value,
        agent_state=mode.value,
        schedule_display=result.schedule_display,
        tool_trace=ctx.trace,
        validator_errors=result.apply_errors,
        debug=debug,
        api_usage=usage,
    )
