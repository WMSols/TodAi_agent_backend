"""
schedule_delete.py — intent: remove or cancel calendar events

  - Specialist may return remove operations → guarded apply + confirmation
"""

from __future__ import annotations

from todai.agent.core.intents._shared import specialist_with_calendar_apply
from todai.agent.core.schedule_display import build_week_schedule_display
from todai.agent.core.types import IntentResult, TurnContext


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "schedule_delete"})
    reply, applied, spec_dbg, apply_errors, months, _guard_trace, _operations = specialist_with_calendar_apply(
        ctx, route="schedule_delete"
    )

    if not reply:
        reply = "Which event or day should I remove?"

    display = None
    if months and not apply_errors:
        display = build_week_schedule_display(ctx.store, ctx.full_index, user_id=ctx.user_id)

    return IntentResult(
        reply_text=reply,
        operations=applied,
        schedule_display=display,
        specialist_dbg=spec_dbg,
        apply_errors=apply_errors,
        months_written=months,
    )
