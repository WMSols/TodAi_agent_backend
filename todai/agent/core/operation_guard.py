"""
operation_guard.py — strict apply rules for specialist calendar operations

  - Never apply when replyText is still asking the user something
  - Validate ops are complete before apply
  - Build server confirmation after successful add/remove
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from todai.agent.tools.calendar import CalendarService, apply_operations_direct, parse_iso_dt
from todai.agent.tools.scheduling import find_conflicts_for_operation
from todai.database.storage import UserStore

# Reply heuristics (kept here so planner/llm can import without pulling the orchestrator).
_CLARIFY_MARKERS = re.compile(
    r"\?|"
    r"\bwhich\b|\bwhat time\b|\bwhat day\b|\bwhen would\b|\bdo you want\b|"
    r"\bplease (?:confirm|specify|tell)\b|\bcan you (?:give|provide|clarify)\b|"
    r"\blet me know\b|\bstill need\b|\banything else\b",
    re.I,
)
_SOFT_CONFIRM = re.compile(r"\bplease confirm\b", re.I)


def reply_is_clarifying(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    return bool(_CLARIFY_MARKERS.search(text))


def reply_blocks_apply(route: str, reply: str) -> bool:
    """True when specialist reply should not trigger apply (real questions only)."""
    if route not in ("schedule_write", "schedule_delete"):
        return False
    text = (reply or "").strip()
    if not text:
        return False
    if "?" in text:
        return True
    stripped = _SOFT_CONFIRM.sub("", text).strip()
    if not stripped:
        return False
    return bool(
        re.search(
            r"\b(?:which|what time|what day|when would|do you want|still need|let me know|"
            r"please (?:specify|tell)|can you (?:give|provide|clarify)|anything else)\b",
            stripped,
            re.I,
        )
    )


_USER_TIME_HINT = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|\b(?:am|pm)\s*to\b",
    re.I,
)


def validate_operation(op: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(op, dict):
        return [{"code": "INVALID_OP", "detail": "not an object"}]
    kind = str(op.get("op") or "").lower()
    if kind == "remove":
        bid = str(op.get("id") or "").strip()
        if not bid or bid.lower() in ("null", "none"):
            errors.append({"code": "MISSING_ID", "detail": "remove requires block id"})
    elif kind == "add":
        for field in ("start", "end", "title"):
            if not str(op.get(field) or "").strip():
                errors.append({"code": "MISSING_FIELD", "detail": f"add requires {field}"})
    elif kind == "update":
        if not str(op.get("id") or "").strip():
            errors.append({"code": "MISSING_ID", "detail": "update requires block id"})
        if not str(op.get("start") or "").strip() or not str(op.get("end") or "").strip():
            errors.append({"code": "MISSING_FIELD", "detail": "update requires start and end"})
    else:
        errors.append({"code": "UNKNOWN_OP", "detail": kind or "missing op"})
    return errors


def validate_add_in_scope(
    op: dict[str, Any],
    resolved_scope: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Reject add ops whose calendar day falls outside resolved_scope (14-day agent window)."""
    if not resolved_scope:
        return []
    if str(op.get("op") or "").lower() != "add":
        return []
    scope_from = str(resolved_scope.get("from") or "")[:10]
    scope_to = str(resolved_scope.get("to") or "")[:10]
    if not scope_from or not scope_to:
        return []
    try:
        start_day = parse_iso_dt(str(op.get("start") or "")).date().isoformat()
    except ValueError:
        return []
    if start_day < scope_from or start_day > scope_to:
        return [
            {
                "code": "OUT_OF_SCOPE",
                "detail": f"event date {start_day} outside scope {scope_from}–{scope_to}",
            }
        ]
    return []


def validate_add_time_range(
    op: dict[str, Any],
    *,
    user_message: str = "",
    resolved_scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reject specialist add ops with impossible or clearly wrong times (does not rewrite times)."""
    base = validate_operation(op)
    if base:
        return base
    if str(op.get("op") or "").lower() != "add":
        return []
    try:
        start = parse_iso_dt(str(op["start"]))
        end = parse_iso_dt(str(op["end"]))
    except ValueError:
        return [{"code": "INVALID_TIME", "detail": "start/end must be ISO datetimes"}]
    if user_message and _USER_TIME_HINT.search(user_message):
        if start.hour == 0 and start.minute == 0 and end.hour == 0 and end.minute == 0:
            return [
                {
                    "code": "TIME_MISMATCH",
                    "detail": "start/end look like midnight but user gave am/pm times",
                }
            ]
    if end <= start:
        return [{"code": "INVALID_RANGE", "detail": "end must be after start"}]
    if (end - start).total_seconds() < 60:
        return [{"code": "INVALID_RANGE", "detail": "event must be at least one minute"}]
    scope_errs = validate_add_in_scope(op, resolved_scope)
    if scope_errs:
        return scope_errs
    return []


def validate_schedule_conflict(
    op: dict[str, Any],
    store: UserStore,
) -> list[dict[str, Any]]:
    """Reject add/update when the time slot overlaps an existing event."""
    kind = str(op.get("op") or "").lower()
    if kind not in ("add", "update"):
        return []
    try:
        start = parse_iso_dt(str(op["start"]))
        end = parse_iso_dt(str(op["end"]))
    except (KeyError, ValueError):
        return []
    existing = CalendarService(store).get_events(start.date(), end.date())
    conflicts = find_conflicts_for_operation(op, existing)
    if not conflicts:
        return []
    c0 = conflicts[0]
    title = str(c0.get("title") or "another event")
    try:
        cs = parse_iso_dt(str(c0["start"]))
        ce = parse_iso_dt(str(c0["end"]))
        slot = f"{cs.strftime('%A %d %B')}, {_clock(cs)}–{_clock(ce)}"
    except (KeyError, ValueError):
        slot = "that time"
    return [
        {
            "code": "SLOT_CONFLICT",
            "detail": f"{slot} is already booked with {title}",
            "conflict": c0,
        }
    ]


def _remove_op_in_scope(
    op: dict[str, Any],
    resolved_scope: dict[str, Any] | None,
    store: UserStore,
) -> bool:
    if str(op.get("op") or "").lower() != "remove":
        return True
    if not resolved_scope:
        return True
    fr = str(resolved_scope.get("from") or "")[:10]
    to = str(resolved_scope.get("to") or "")[:10]
    if not fr or not to:
        return True
    blk = _find_block(store, str(op.get("id") or ""))
    if not blk:
        return False
    try:
        day = parse_iso_dt(str(blk.get("start") or "")).date().isoformat()
    except ValueError:
        return False
    return fr <= day <= to


def filter_operations_for_apply(
    route: str,
    reply: str,
    operations: list[dict[str, Any]],
    *,
    user_message: str = "",
    resolved_scope: dict[str, Any] | None = None,
    store: UserStore | None = None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    if not operations:
        return [], None, None
    if route in ("schedule_write", "schedule_delete") and reply_blocks_apply(route, reply):
        return [], "clarifying_reply", None
    valid: list[dict[str, Any]] = []
    slot_conflict_detail: dict[str, Any] | None = None
    had_time_errors = False
    for op in operations:
        errs = (
            validate_add_time_range(op, user_message=user_message, resolved_scope=resolved_scope)
            if route == "schedule_write"
            else validate_operation(op)
        )
        if errs:
            had_time_errors = True
        if not errs and route == "schedule_write" and store is not None:
            errs = validate_schedule_conflict(op, store)
            if errs:
                slot_conflict_detail = errs[0]
        if not errs and route == "schedule_delete" and store is not None:
            if not _remove_op_in_scope(op, resolved_scope, store):
                errs = [{"code": "OUT_OF_SCOPE", "detail": "remove outside resolved day"}]
        if not errs:
            valid.append(op)
    if not valid:
        if slot_conflict_detail:
            return [], "slot_conflict", slot_conflict_detail
        if route == "schedule_write" and operations and had_time_errors:
            return [], "invalid_times", None
        return [], "invalid_operations", None
    return valid, None, None


def _find_block(store: UserStore, block_id: str) -> dict[str, Any] | None:
    return store.find_block(block_id)


def _clock(dt: datetime) -> str:
    h12 = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    if dt.minute == 0:
        return f"{h12} {suffix}"
    return f"{h12}:{dt.minute:02d} {suffix}"


def _fmt_event_line(title: str, start_s: str, end_s: str) -> str:
    try:
        start = parse_iso_dt(start_s)
        end = parse_iso_dt(end_s)
    except ValueError:
        return title
    return f"{title} on {start.strftime('%A')} {start.strftime('%d %B')}, {_clock(start)}–{_clock(end)}"


def confirmation_message(
    operations: list[dict[str, Any]],
    *,
    route: str,
    removed_before: dict[str, dict[str, Any]] | None = None,
) -> str:
    parts: list[str] = []
    removed_before = removed_before or {}
    for op in operations:
        kind = str(op.get("op") or "").lower()
        if kind == "remove":
            bid = str(op.get("id"))
            blk = removed_before.get(bid)
            if blk:
                parts.append(
                    "Removed "
                    + _fmt_event_line(str(blk.get("title") or "Event"), str(blk["start"]), str(blk["end"]))
                    + "."
                )
            else:
                parts.append(f"Removed event (id {bid}).")
        elif kind == "add":
            parts.append(
                "Added "
                + _fmt_event_line(str(op.get("title") or "Event"), str(op.get("start", "")), str(op.get("end", "")))
                + "."
            )
        elif kind == "update":
            parts.append(f"Updated {str(op.get('title') or 'event')}.")
    if not parts:
        return "Done — your calendar was updated." if route == "schedule_write" else "Done — I removed that from your calendar."
    return " ".join(parts)


def apply_with_guard(
    store: UserStore,
    *,
    route: str,
    reply: str,
    operations: list[dict[str, Any]],
    user_message: str = "",
    resolved_scope: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int, dict[str, Any]]:
    trace: dict[str, Any] = {}
    to_apply, block, block_detail = filter_operations_for_apply(
        route,
        reply,
        operations,
        user_message=user_message,
        resolved_scope=resolved_scope,
        store=store,
    )
    if block:
        trace["apply_blocked"] = block
        trace["dropped_operations"] = len(operations)
        if block_detail:
            trace["block_detail"] = block_detail
        if block == "invalid_times":
            reply = (
                "I couldn't save that — the times didn't look right. "
                "Please say the day and a clear range (for example Saturday 10 pm to 11 pm)."
            )
        elif block == "slot_conflict":
            detail = str((block_detail or {}).get("detail") or "That time slot is already booked.")
            reply = (
                f"I can't add that — {detail}. "
                "Choose a different time or ask me to move or replace the existing event."
            )
        return reply, [], [], 0, trace

    if not to_apply:
        return reply, [], [], 0, trace

    removed_before: dict[str, dict[str, Any]] = {}
    for op in to_apply:
        if str(op.get("op")).lower() == "remove":
            bid = str(op.get("id"))
            blk = _find_block(store, bid)
            if blk:
                removed_before[bid] = blk

    apply_errors, months = apply_operations_direct(store, to_apply)
    trace["months_written"] = months
    if apply_errors or not months:
        return reply, to_apply, apply_errors, months, trace

    confirm = confirmation_message(to_apply, route=route, removed_before=removed_before)
    if reply_is_clarifying(reply) or not reply.strip():
        final_reply = confirm
    elif confirm.lower() not in reply.lower():
        final_reply = confirm
    else:
        final_reply = reply
    return final_reply, to_apply, apply_errors, months, trace
