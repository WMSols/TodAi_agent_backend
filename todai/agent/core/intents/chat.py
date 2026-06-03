"""
chat.py — intent: general conversation (no calendar writes)

  - Specialist prompt: friendly chat only, operations []
  - Prefetch: only if router asked for read tools (usually none)
"""

from __future__ import annotations

from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.types import IntentResult, TurnContext
from todai.agent.routing.goal_redirect import (
    GOAL_PLANNER_REDIRECT_REPLY,
    should_redirect_to_goal_planner,
)


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "chat"})
    if should_redirect_to_goal_planner(ctx.message):
        ctx.trace.append({"phase": "goal_planner_redirect"})
        return IntentResult(reply_text=GOAL_PLANNER_REDIRECT_REPLY, operations=[])
    reply, operations, spec_dbg = run_specialist(ctx)
    if not reply:
        reply = "How can I help with your schedule?"
    return IntentResult(reply_text=reply, operations=operations, specialist_dbg=spec_dbg)
