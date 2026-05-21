"""
intents/ — one handler module per router intent

  chat              → intents/chat.py
  schedule_preview  → intents/schedule_preview.py
  schedule_write    → intents/schedule_write.py
  schedule_delete   → intents/schedule_delete.py
"""

from __future__ import annotations

from collections.abc import Callable

from todai.agent.planner.llm import AgentRoute
from todai.agent.core.intents import chat, schedule_delete, schedule_preview, schedule_write
from todai.agent.core.types import IntentResult, TurnContext

IntentHandler = Callable[[TurnContext], IntentResult]

INTENT_HANDLERS: dict[AgentRoute, IntentHandler] = {
    AgentRoute.CHAT: chat.handle,
    AgentRoute.SCHEDULE_PREVIEW: schedule_preview.handle,
    AgentRoute.SCHEDULE_WRITE: schedule_write.handle,
    AgentRoute.SCHEDULE_DELETE: schedule_delete.handle,
}


def dispatch(ctx: TurnContext) -> IntentResult:
    handler = INTENT_HANDLERS.get(ctx.route)
    if handler is None:
        return chat.handle(ctx)
    return handler(ctx)
