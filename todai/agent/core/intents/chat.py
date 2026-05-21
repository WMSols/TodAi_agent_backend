"""
chat.py — intent: general conversation (no calendar writes)

  - Specialist prompt: friendly chat only, operations []
  - Prefetch: only if router asked for read tools (usually none)
"""

from __future__ import annotations

from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.types import IntentResult, TurnContext


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "chat"})
    reply, operations, spec_dbg = run_specialist(ctx)
    if not reply:
        reply = "How can I help with your schedule?"
    return IntentResult(reply_text=reply, operations=operations, specialist_dbg=spec_dbg)
