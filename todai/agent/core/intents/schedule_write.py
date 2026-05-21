"""
schedule_write.py — intent: add, move, or change calendar events

  - Specialist may return operations → guarded apply + confirmation
  - Reply only claims success when months_written > 0
"""

from __future__ import annotations

import re
from typing import Any

from todai.agent.core.clarify import reply_is_clarifying
from todai.agent.core.intents._shared import run_specialist
from todai.agent.core.operation_guard import apply_with_guard
from todai.agent.core.refresh_display import build_week_schedule_display
from todai.agent.core.types import IntentResult, TurnContext

_CLAIMS_SAVED = re.compile(
    r"\b(?:added|removed|updated|saved|booked)\b",
    re.I,
)


def _write_failed_reply(*, had_operations: bool, apply_errors: list[dict[str, Any]]) -> str:
    if apply_errors:
        detail = str(apply_errors[0].get("detail", ""))[:120]
        return f"I couldn't save that to your calendar ({detail or 'apply error'}). Please try again with day, time, and title."
    if had_operations:
        return "I couldn't save that to your calendar yet. Please try again with the day, time, and event title."
    return "Tell me what you'd like to add or change — include day, time, and title."


def handle(ctx: TurnContext) -> IntentResult:
    ctx.trace.append({"phase": "intent", "intent": "schedule_write"})
    reply, operations, spec_dbg = run_specialist(ctx)
    ctx.trace.append({"phase": "specialist", "operation_count": len(operations)})

    resolved_scope = ctx.preview_range.as_dict() if ctx.preview_range else None
    reply, applied, apply_errors, months, guard_trace = apply_with_guard(
        ctx.store,
        route="schedule_write",
        reply=reply,
        operations=operations,
        user_message=ctx.message,
        resolved_scope=resolved_scope,
    )
    if guard_trace:
        ctx.trace.append({"phase": "direct_apply", **guard_trace, "errors": apply_errors})

    if months and not apply_errors:
        if not reply:
            reply = "Done — your calendar was updated."
    elif reply_is_clarifying(reply):
        pass
    elif operations or (reply and _CLAIMS_SAVED.search(reply)):
        reply = _write_failed_reply(had_operations=bool(operations), apply_errors=apply_errors)
        ctx.trace.append({"phase": "write_not_saved", "months": months})
    elif not reply:
        reply = "Tell me what you'd like to add or change on your calendar."

    display = None
    if months and not apply_errors:
        display = build_week_schedule_display(ctx.store, ctx.full_index)

    return IntentResult(
        reply_text=reply,
        operations=applied,
        schedule_display=display,
        specialist_dbg=spec_dbg,
        apply_errors=apply_errors,
        months_written=months,
    )
