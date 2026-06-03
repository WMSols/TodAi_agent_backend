"""
schedule_delete.py — intent: remove or cancel calendar events

  - Specialist may return remove operations → guarded apply + confirmation
"""

from __future__ import annotations

from typing import Any

from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.operation_guard import apply_with_guard
from todai.agent.core.refresh_display import build_week_schedule_display
from todai.agent.core.types import IntentResult, TurnContext


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "schedule_delete"})
    reply, operations, spec_dbg = run_specialist(ctx)
    ctx.trace.append({"phase": "specialist", "operation_count": len(operations)})

    resolved_scope = ctx.preview_range.as_dict() if ctx.preview_range else None
    reply, applied, apply_errors, months, guard_trace = apply_with_guard(
        ctx.store,
        route="schedule_delete",
        reply=reply,
        operations=operations,
        resolved_scope=resolved_scope,
    )
    if guard_trace:
        ctx.trace.append({"phase": "direct_apply", **guard_trace, "errors": apply_errors})

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
