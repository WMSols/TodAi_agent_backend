"""
schedule_preview.py — intent: show / preview what's on the calendar

  Preview window comes from preview_range (explicit day/week/month or default week).
  Never applies calendar writes; reply must not claim add/remove succeeded.
"""

from __future__ import annotations

import re
from typing import Any

from todai.agent.core.goal_overlay import build_schedule_display_with_goals
from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.groq_errors import specialist_groq_failed
from todai.agent.core.schedule_display import (
    _empty_day_row,
    build_free_days_period_reply,
    build_grounded_preview_reply,
    build_period_preview_reply,
    pick_schedule_assistant_text,
)
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

    display = build_schedule_display_with_goals(
        ctx.read_results,
        user_id=ctx.user_id,
        period_from=preview.date_from,
        period_to=preview.date_to,
        fill_empty_days=preview.fill_empty_days,
        title=preview.label,
        show_free_banners=preview.show_free_banners,
    )
    if display and preview.scope_mode == "discrete_days" and preview.target_days:
        allowed = set(preview.target_days)
        days = display.get("days") or []
        by_date = {str(d.get("date", ""))[:10]: d for d in days}
        from datetime import date as date_cls, datetime, time

        filtered: list[dict[str, Any]] = []
        for day_iso in preview.target_days:
            if day_iso in by_date:
                filtered.append(by_date[day_iso])
            else:
                try:
                    filtered.append(_empty_day_row(datetime.combine(date_cls.fromisoformat(day_iso), time.min)))
                except ValueError:
                    continue
        display = {**display, "days": filtered, "period": {"from": preview.target_days[0], "to": preview.target_days[-1]}}
    if display and display.get("days"):
        ctx.trace.append({"phase": "goal_overlay", "merged": True})

    reply, operations, spec_dbg = run_specialist(ctx)
    if operations:
        ctx.trace.append({"phase": "preview", "dropped_operations": len(operations)})

    groq_failed = specialist_groq_failed(spec_dbg, reply)
    grounded = build_grounded_preview_reply(
        message=ctx.message,
        read_results=ctx.read_results,
        preview=preview,
    )
    if grounded:
        reply = grounded
        ctx.trace.append({"phase": "preview_reply", "source": "grounded"})
    elif groq_failed and ctx.read_results:
        period_reply = build_period_preview_reply(ctx.read_results, preview)
        if not period_reply:
            period_reply = build_free_days_period_reply(ctx.message, ctx.read_results)
        if period_reply:
            reply = period_reply
            ctx.trace.append({"phase": "preview_reply", "source": "prefetch_fallback"})
        else:
            reply = pick_schedule_assistant_text("", display)
            ctx.trace.append({"phase": "preview_reply", "source": "display_fallback"})
    elif not groq_failed:
        period_reply = build_free_days_period_reply(ctx.message, ctx.read_results)
        if period_reply:
            reply = period_reply
            ctx.trace.append({"phase": "preview_reply", "source": "grounded_free_days"})

    reply = _sanitize_preview_reply(reply)
    if not reply:
        reply = pick_schedule_assistant_text("Here's your schedule below.", display)

    return IntentResult(
        reply_text=reply,
        operations=[],
        schedule_display=display,
        specialist_dbg=spec_dbg,
    )
