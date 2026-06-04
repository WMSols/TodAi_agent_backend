"""
chat.py — intent: general conversation (no calendar writes)

  - Specialist prompt: friendly chat only, operations []
  - Prefetch: only if router asked for read tools (usually none)
"""

from __future__ import annotations

from todai.agent.core.groq_errors import specialist_groq_failed
from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.types import IntentResult, TurnContext
from todai.agent.routing.routing_guards import (
    GOAL_PLANNER_REDIRECT_REPLY,
    is_plain_chat_message,
    should_redirect_to_goal_planner,
)


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "chat"})
    if should_redirect_to_goal_planner(ctx.message):
        ctx.trace.append({"phase": "goal_planner_redirect"})
        return IntentResult(reply_text=GOAL_PLANNER_REDIRECT_REPLY, operations=[])
    reply, operations, spec_dbg = run_specialist(ctx)
    if specialist_groq_failed(spec_dbg, reply):
        ctx.trace.append({"phase": "chat_reply", "source": "local_fallback"})
        if is_plain_chat_message(ctx.message):
            reply = "I'm here when you're ready — ask about your schedule anytime."
        else:
            reply = "I couldn't reach the AI right now. Try again in a minute, or ask to preview your schedule."
    elif not reply:
        reply = "How can I help with your schedule?"
    return IntentResult(reply_text=reply, operations=operations, specialist_dbg=spec_dbg)
