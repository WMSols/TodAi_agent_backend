"""
prompt_bundles.py — token-optimized router/specialist context (legacy builders in llm.py as comments).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from todai.agent.routing.preview_range import (
    AGENT_WINDOW_DAYS,
    agent_window_as_dict,
    user_request_outside_agent_window,
)
# Cap specialist calendar JSON (~3k chars ≈ under 1k tokens for typical weeks)
_SPECIALIST_BLOCKS_CAP = 4500
_MONTH_DIGEST_THRESHOLD = 18


def slim_router_anchor(date_anchor: dict[str, Any] | None, *, today: date | None = None) -> dict[str, Any]:
    if not date_anchor:
        return {}
    out: dict[str, Any] = {
        "today": date_anchor.get("today"),
        "weekday_lookup": date_anchor.get("weekday_lookup"),
    }
    if today is not None:
        out["agent_window"] = agent_window_as_dict(today)
    if date_anchor.get("mentioned_weekdays"):
        out["mentioned_weekdays"] = date_anchor["mentioned_weekdays"]
    if date_anchor.get("weekday_candidates"):
        out["weekday_candidates"] = date_anchor["weekday_candidates"]
    month = date_anchor.get("month") or {}
    if month:
        out["month"] = {
            "ym": month.get("ym"),
            "first_day": month.get("first_day"),
            "last_day": month.get("last_day"),
        }
    return out


def slim_date_anchor_for_specialist(route: str, date_anchor: dict[str, Any] | None) -> dict[str, Any] | None:
    if not date_anchor or route == "chat":
        return None
    if route in ("schedule_write", "schedule_delete"):
        slim = slim_router_anchor(date_anchor)
        if route == "schedule_write":
            slim["rolling_days"] = date_anchor.get("rolling_days") or []
        return slim
    return slim_router_anchor(date_anchor)


def _schedule_digest(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact per-day summary when a month has many events."""
    by_day: dict[str, list[str]] = {}
    for b in blocks:
        start = str(b.get("start", ""))[:10]
        if not start:
            continue
        by_day.setdefault(start, []).append(str(b.get("title") or "Event"))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day.keys()):
        titles = by_day[day]
        rows.append({"date": day, "count": len(titles), "titles": titles[:6]})
    return rows


def extract_schedule_bundle(
    read_results: list[dict[str, Any]],
    *,
    scope_granularity: str | None = None,
) -> dict[str, Any] | None:
    """One calendar slice from prefetch — no duplicate highlights blob."""
    for r in read_results:
        if r.get("tool") != "get_schedule_range" or not r.get("ok"):
            continue
        data = r.get("data") or {}
        blocks = data.get("blocks") or []
        slim_blocks = [
            {
                "id": b.get("id"),
                "title": b.get("title"),
                "start": b.get("start"),
                "end": b.get("end"),
            }
            for b in blocks
        ]
        out: dict[str, Any] = {
            "from": str(data.get("from", ""))[:10],
            "to": str(data.get("to", ""))[:10],
            "blocks": slim_blocks,
            "empty": len(slim_blocks) == 0,
        }
        if (
            scope_granularity == "month"
            and len(slim_blocks) > _MONTH_DIGEST_THRESHOLD
        ):
            out["digest"] = _schedule_digest(slim_blocks)
            out["blocks"] = slim_blocks[:12]
        return out
    return None


def slim_block_index(full_index: dict[str, Any]) -> list[dict[str, str]]:
    """Minimal id+title for delete matching."""
    rows: list[dict[str, str]] = []
    for cf in full_index.get("calendar_files") or []:
        if not isinstance(cf, dict):
            continue
        for b in cf.get("blocks") or []:
            if isinstance(b, dict) and b.get("id"):
                rows.append({"id": str(b["id"]), "title": str(b.get("title", ""))})
    if rows:
        return rows
    return [{"id": i} for i in (full_index.get("known_block_ids") or [])[:40]]


def build_turn_facts(
    *,
    route: str,
    current_message: str,
    date_anchor: dict[str, Any] | None,
    read_results: list[dict[str, Any]],
    preview_range: dict[str, Any] | None,
    server_snapshot: dict[str, Any],
    full_index: dict[str, Any] | None,
    last_agent_mode: str | None,
) -> dict[str, Any]:
    today_s = (server_snapshot.get("server_date_utc") or "")[:10]
    try:
        today = date.fromisoformat(today_s)
    except ValueError:
        today = date.today()

    facts: dict[str, Any] = {
        "intent": route,
        "user_message": (current_message or "").strip(),
        "server_today": today_s,
        "last_agent_mode": last_agent_mode,
        "agent_window": agent_window_as_dict(today),
    }
    if route != "chat" and user_request_outside_agent_window(
        current_message, today, date_anchor
    ):
        facts["outside_agent_window"] = True
    anchor = slim_date_anchor_for_specialist(route, date_anchor)
    if anchor is not None and route != "chat":
        anchor["agent_window"] = facts["agent_window"]
    if anchor:
        facts["dates"] = anchor
    scope_granularity = (preview_range or {}).get("granularity") if preview_range else None
    sched = extract_schedule_bundle(read_results, scope_granularity=scope_granularity)
    if sched:
        facts["schedule"] = sched
    if preview_range:
        facts["resolved_scope"] = preview_range
        if route == "schedule_preview":
            facts["preview_scope"] = preview_range
    if route == "schedule_delete" and full_index:
        facts["event_index"] = slim_block_index(full_index)[:40]
    goals = next(
        (r.get("data") for r in read_results if r.get("tool") == "get_active_goals" and r.get("ok")),
        None,
    )
    if goals:
        facts["goals"] = goals
    if route == "schedule_write":
        aw = facts["agent_window"]
        wk_cand = (date_anchor or {}).get("weekday_candidates") or {}
        day_rule = "use dates.mentioned_weekdays for day when set and unambiguous"
        if wk_cand:
            facts["weekday_candidates"] = wk_cand
            day_rule = (
                "weekday_candidates means multiple dates match (e.g. two Fridays); "
                "operations [] and ask which date (list each label); do not pick one yourself"
            )
        facts["write_rules"] = (
            "add op: start/end ISO local times from user_message; "
            f"event date MUST be within agent_window ({aw['from']}–{aw['to']}, {AGENT_WINDOW_DAYS} days); "
            "if outside_agent_window, operations [] and explain you cannot save outside that range; "
            f"{day_rule}; if only duration (e.g. one hour) without clock time, operations [] and ask start/end times"
        )
    if route == "schedule_preview" and facts.get("outside_agent_window"):
        facts["preview_rules"] = (
            f"operations []. Say you can only show the next {AGENT_WINDOW_DAYS} days from server_today "
            "and cannot view dates outside agent_window."
        )
    if route == "schedule_delete" and facts.get("outside_agent_window"):
        facts["delete_rules"] = (
            f"operations []. Say you can only remove events in the next {AGENT_WINDOW_DAYS} days."
        )
    return facts


def build_router_user_context_slim(
    *,
    current_message: str,
    conversation: dict[str, Any],
    date_anchor: dict[str, Any] | None,
    server_snapshot: dict[str, Any] | None = None,
) -> str:
    today: date | None = None
    if server_snapshot:
        try:
            today = date.fromisoformat(str(server_snapshot.get("server_date_utc", ""))[:10])
        except ValueError:
            today = None
    return (
        "CURRENT_USER_MESSAGE:\n"
        + (current_message or "").strip()
        + "\n\nROUTING_HINTS:\n"
        + json.dumps(
            {
                "dates": slim_router_anchor(date_anchor, today=today),
                "last_agent_mode": conversation.get("last_agent_mode"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        + "\n\nRespond with JSON only (route + tools). No user reply text."
    )


def build_specialist_user_payload_slim(turn_facts: dict[str, Any]) -> str:
    blob = json.dumps(turn_facts, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(blob) > _SPECIALIST_BLOCKS_CAP and "schedule" in turn_facts:
        trimmed = dict(turn_facts)
        sched = dict(trimmed.get("schedule") or {})
        blocks = list(sched.get("blocks") or [])
        while len(blob) > _SPECIALIST_BLOCKS_CAP and len(blocks) > 3:
            blocks = blocks[: len(blocks) - 1]
            sched["blocks"] = blocks
            trimmed["schedule"] = sched
            blob = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"), default=str)
    return (
        "TURN_FACTS:\n"
        + blob
        + "\n\nWrite ONE JSON object with replyText (answer user_message) and operations. "
        "JSON only — no prose outside the object. Conversation above is tone/follow-ups only."
    )
