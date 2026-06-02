"""
schedule_preview.py — intent: show / preview what's on the calendar

  Preview window comes from preview_range (explicit day/week/month or default week).
  Never applies calendar writes; reply must not claim add/remove succeeded.
"""

from __future__ import annotations

import re

from todai.agent.core.display import build_schedule_display
from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.preview_reply import build_free_days_period_reply, build_grounded_preview_reply
from todai.agent.routing.preview_range import resolve_preview_range
from todai.agent.core.types import IntentResult, TurnContext

_CLAIMS_WRITE = re.compile(
    r"\b(?:added|removed|updated|saved|booked|created|deleted|cancelled|canceled)\b",
    re.I,
)


def _sanitize_preview_reply(reply: str) -> str:
    """Preview route must not sound like a calendar write succeeded."""
    if not reply or not _CLAIMS_WRITE.search(reply):
        return reply
    return (
        "I can show your schedule below, but I can't save changes from a view request. "
        "To add or change an event, say something like: add dance party on Sunday at 1 pm."
    )


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "schedule_preview"})

    preview = ctx.preview_range or resolve_preview_range(
        ctx.message, ctx.date_anchor, full_index=ctx.full_index
    )

    reply, operations, spec_dbg = run_specialist(ctx)
    if operations:
        ctx.trace.append({"phase": "preview", "dropped_operations": len(operations)})
    grounded = build_grounded_preview_reply(
        message=ctx.message,
        read_results=ctx.read_results,
        preview=preview,
    )
    if grounded:
        reply = grounded
        ctx.trace.append({"phase": "preview_reply", "source": "grounded"})
    elif not grounded:
        period_reply = build_free_days_period_reply(ctx.message, ctx.read_results)
        if period_reply:
            reply = period_reply
            ctx.trace.append({"phase": "preview_reply", "source": "grounded_free_days"})
    reply = _sanitize_preview_reply(reply)
    if not reply:
        reply = "Here's your schedule below."

    display = build_schedule_display(
        ctx.read_results,
        period_from=preview.date_from,
        period_to=preview.date_to,
        fill_empty_days=preview.fill_empty_days,
        title=preview.label,
        show_free_banners=preview.show_free_banners,
    )

    return IntentResult(
        reply_text=reply,
        operations=[],
        schedule_display=display,
        specialist_dbg=spec_dbg,
    )
