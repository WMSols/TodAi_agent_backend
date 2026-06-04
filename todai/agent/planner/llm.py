"""
llm.py — Groq HTTP, prompts, router + specialist calls

Sections:
  1. Prompt templates (router + per-route specialist system text)
  2. Groq chat/completions + JSON extraction
  3. route_turn — small model picks route + optional read tools
  4. specialist_turn — larger reply + optional calendar operations
  5. Mock fallbacks when GROQ_API_KEY is unset
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from todai.agent.core.operation_guard import reply_is_clarifying
from todai.agent.routing.preview_range import AGENT_WINDOW_DAYS, agent_window_bounds
from todai.api.middleware.rate_limit import TurnAllowance, current_turn_user_id, groq_tracker, rate_limit_user_message
from todai.agent.planner.groq_config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL
from todai.api.logging import logger
from todai.database.utils.dates import parse_server_date

# ── Router contract (parsed from Groq JSON) ───────────────────────────────


class AgentRoute(str, Enum):
    CHAT = "chat"
    SCHEDULE_PREVIEW = "schedule_preview"
    SCHEDULE_WRITE = "schedule_write"
    SCHEDULE_DELETE = "schedule_delete"


_VALID_ROUTES = {r.value for r in AgentRoute}


class RouterOutput(BaseModel):
    route: str = "chat"
    time_scope: str = "default"
    tools: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("time_scope", mode="before")
    @classmethod
    def _norm_time_scope(cls, v: Any) -> str:
        from todai.agent.routing.preview_range import normalize_time_scope

        return normalize_time_scope(str(v) if v is not None else None)

    @field_validator("route", mode="before")
    @classmethod
    def _norm_route(cls, v: Any) -> str:
        s = str(v or "chat").strip().lower()
        return s if s in _VALID_ROUTES else "chat"

    @property
    def agent_route(self) -> AgentRoute:
        return AgentRoute(self.route)


def parse_router_output(raw: dict[str, Any]) -> tuple[RouterOutput | None, list[dict[str, Any]]]:
    from todai.agent.routing.preview_range import normalize_router_tools

    if not isinstance(raw, dict):
        return None, [{"code": "INVALID_ROUTER", "detail": "expected object"}]
    dbg = raw.get("_groq_debug")
    if isinstance(dbg, dict) and dbg.get("ok") is False:
        return None, [{"code": "GROQ_ROUTER_FAILED", "detail": dbg}]
    reply = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
    has_route = bool(raw.get("route") or raw.get("state") or raw.get("prompt"))
    if reply and not has_route and re.search(r"rate limit|groq http", reply, re.I):
        return None, [{"code": "GROQ_RATE_LIMIT", "detail": reply[:200]}]
    normalized = {
        "route": raw.get("route") or raw.get("state") or raw.get("prompt"),
        "time_scope": raw.get("time_scope") or raw.get("scope") or raw.get("timeScope") or "default",
        "tools": normalize_router_tools(raw.get("tools") or raw.get("toolPlan")),
    }
    try:
        return RouterOutput.model_validate(normalized), []
    except Exception as e:
        return None, [{"code": "INVALID_ROUTER", "detail": str(e)}]


# ── 1. Prompts ────────────────────────────────────────────────────────────

ROUTER_JSON_CONTRACT = (
    'JSON only: {"route": string, "time_scope": string, "tools": array}\n'
    "route: chat | schedule_preview | schedule_write | schedule_delete\n"
    "time_scope: default | today | tomorrow | this_week | next_week | single_day | free_days | free_time\n"
    'tools: ["get_schedule_range"] or [{"tool":"get_schedule_range"}]; NO from/to in tools.\n'
    "Tools: get_schedule_range, get_free_time, get_days_without_schedule, get_active_goals\n"
)

# Compact scenario map (server maps time_scope → dates; see ROUTING_HINTS.weekdays).
ROUTER_TIME_SCOPE_RULES = (
    "time_scope (pick one):\n"
    "today — today's schedule; tomorrow — tomorrow only;\n"
    "this_week — rest of current calendar week (today→Sun);\n"
    "next_week — FULL next Mon–Sun only (next week|next all week|coming week|add every day next week);\n"
    "single_day — ONE day: on/for weekday, ISO date, next monday|next saturday (NOT next_week);\n"
    "free_days + get_days_without_schedule — days with zero events;\n"
    "free_time + get_free_time — gap slots; default — vague/all/upcoming/what's on (14d window).\n"
)

# --- Token-optimized (active) ---
ROUTER_SYSTEM = (
    "TodAI router. CURRENT_USER_MESSAGE + ROUTING_CONTEXT for short follow-ups.\n"
    + ROUTER_JSON_CONTRACT
    + ROUTER_TIME_SCOPE_RULES
    + "chat: tools [], time_scope default. preview: read calendar. write: add/move/time. delete: remove.\n"
    "Goals (create/delete/7-day plans/list goals): user uses Goal planner tab, not calendar.\n"
    "Do not use next_week for next <weekday> alone. Output JSON only.\n"
)

SPECIALIST_JSON_CONTRACT = (
    "JSON only: reply with ONE JSON object: "
    '{"replyText": string, "operations": array}\n'
    "Put all user-facing text in replyText. operations [] if reply asks anything (?). "
    "ops only when add/remove is fully specified.\n"
    "add: {op:add,start,end,title}. remove: {op:remove,id} from TURN_FACTS schedule/event_index.\n"
    "No text outside the JSON object.\n"
)

SPECIALIST_SYSTEM_CHAT = (
    "TodAI chat. operations []. Put greeting/answer in replyText JSON field; "
    "use conversation for tone only. Do not claim calendar saves unless user asks about scheduling.\n"
)

SPECIALIST_SYSTEM_PREVIEW = (
    f"TodAI preview. operations []. Only the next {AGENT_WINDOW_DAYS} days from server_today (agent_window) exist for you. "
    "Follow preview_read_kind and preview_rules in TURN_FACTS. "
    "days_without_schedule = whole days with zero events; free_time = gap slots on busy days. "
    "If outside_agent_window is true, operations [] and say clearly you cannot view or change dates outside that window. "
    "If schedule.empty is true, say nothing is scheduled in that period. Do not invent events. "
    "UI shows the table — reply 1–2 short sentences; for free_days list only days from days_without_schedule.days.\n"
)

SPECIALIST_SYSTEM_WRITE = (
    f"TodAI write. Only the next {AGENT_WINDOW_DAYS} days from server_today (agent_window). "
    "When day, start, end, and title are clear in user_message, send add operations immediately — "
    "do not ask the user to confirm. Server saves when operations are valid. "
    "operations [] only when something is missing or ambiguous; then ask once for that detail. "
    "If outside_agent_window is true, operations [] and say you cannot add outside agent_window. "
    "If weekday_candidates is set, operations [] and ask which date (list options). "
    "Use dates.mentioned_weekdays only when exactly one day is resolved. "
    "start and end must be ISO datetimes (YYYY-MM-DDTHH:MM:SS) matching user_message times "
    "(9 am → T09:00:00, not midnight). end after start. "
    "Each event day must be within resolved_scope or agent_window. "
    "For multi-day requests (e.g. every day next week), send one add op per day in resolved_scope. "
    "Do not say you added or saved unless operations includes those add ops.\n"
)

SPECIALIST_SYSTEM_DELETE = (
    f"TodAI delete. Only the next {AGENT_WINDOW_DAYS} days (agent_window). "
    "Match event_index + schedule. resolved_scope is the day or range to act on — "
    "remove only events on that day when scope is one day; do not remove other days in prefetch. "
    "Ambiguous weekday → ops [] and ask which date. Clear match → remove op(s) only for that scope.\n"
)

# --- Legacy prompts (backup — pre token optimization) ---
# ROUTER_JSON_CONTRACT_LEGACY = (
#     'Reply with ONE JSON object only: {"route": string, "tools": array}\n'
#     "- route: chat | schedule_preview | schedule_write | schedule_delete\n"
#     "- tools: optional [{tool, arguments}] — use keys \"from\" and \"to\" (YYYY-MM-DD), NOT start_date/end_date\n"
#     "  Allowed tools: get_schedule_range, get_free_time, get_active_goals\n"
# )
# ROUTER_SYSTEM_LEGACY = (
#     "You are a tiny router for TodAI. Your job is to classify the CURRENT user message only.\n"
#     + ROUTER_JSON_CONTRACT_LEGACY
#     + "Priority (read in order):\n"
#     "1. CURRENT_USER_MESSAGE in the last user block is what you route — not old assistant replies.\n"
#     "2. ROUTING_CONTEXT (if present) is only for short follow-ups (e.g. '10 pm to 12 am' after an add).\n"
#     "3. DATE_ANCHOR maps weekdays and today; use it for tool from/to dates.\n"
#     "Routes:\n"
#     "- chat: greetings, thanks, okay/that's good/sounds good, 'let me ask…', 'how are you', "
#     "'what can you do', general advice (routine, habits) with NO request to show or edit the calendar. "
#     "tools MUST be [].\n"
#     "- schedule_preview: user wants to SEE the calendar (preview, what's on, show schedule, tomorrow, this week, upcoming).\n"
#     "- schedule_write: add/book/create/reschedule, 'schedule X on Sunday', or a time range completing an add.\n"
#     "- schedule_delete: remove/delete/cancel/clear an event.\n"
#     "Examples (CURRENT_USER_MESSAGE → route):\n"
#     '- "okay thats good" → chat\n'
#     '- "whats on friday?" → schedule_preview\n'
#     '- "add dance party sunday 9pm" → schedule_write\n'
#     '- "10 pm to 12 am" (after add thread) → schedule_write\n'
#     "tools: each item must be {tool, arguments:{from,to}}. Do not put from/to on the tool root.\n"
#     "Do not write the user reply. Output JSON only.\n"
# )
# SPECIALIST_JSON_CONTRACT_LEGACY = (
#     'Reply with ONE JSON object only: {"replyText": string, "operations": array}\n'
#     "STRICT (must follow):\n"
#     "- operations and replyText must agree. Never both ask a question AND send operations.\n"
#     "- If replyText asks for info, lists choices, or contains '?', operations MUST be [].\n"
#     "- Only send operations when the user's request is fully specified and unambiguous.\n"
#     "- After a successful action, replyText MUST state what was done (added/removed + title + day/time).\n"
#     "  add: {op:\"add\", start, end, title}\n"
#     "  remove: {op:\"remove\", id} — id from CALENDAR_DATA only\n"
# )


def _format_date_anchor(date_anchor: dict[str, Any] | None) -> str:
    if not date_anchor:
        return ""
    return "DATE_ANCHOR:\n" + json.dumps(date_anchor, ensure_ascii=False, separators=(",", ":"), default=str) + "\n\n"


def _build_router_user_context(
    *,
    current_message: str,
    server_snapshot: dict[str, Any],
    conversation: dict[str, Any],
    date_anchor: dict[str, Any] | None = None,
) -> str:
    from todai.agent.planner.prompt_bundles import build_router_user_context_slim

    return build_router_user_context_slim(
        current_message=current_message,
        conversation=conversation,
        date_anchor=date_anchor,
        server_snapshot=server_snapshot,
    )


# Legacy router context (backup):
# def _build_router_user_context_legacy(...):
#     return (
#         "CURRENT_USER_MESSAGE:\n" + current_message + "\n\n"
#         + _format_date_anchor(date_anchor)
#         + "SERVER_SNAPSHOT:\n" + json.dumps(server_snapshot, ...)
#         + "\nCONVERSATION:\n" + json.dumps(conversation, ...)
#     )


def _build_specialist_system(route: str) -> str:
    extra = {
        "chat": SPECIALIST_SYSTEM_CHAT,
        "schedule_preview": SPECIALIST_SYSTEM_PREVIEW,
        "schedule_write": SPECIALIST_SYSTEM_WRITE,
        "schedule_delete": SPECIALIST_SYSTEM_DELETE,
    }.get(route, SPECIALIST_SYSTEM_CHAT)
    return f"TodAI specialist.\n{SPECIALIST_JSON_CONTRACT}\n{extra}Output JSON only.\n"


# Legacy specialist system (backup):
# def _build_specialist_system_legacy(route: str) -> str:
#     date_rules = "DATE_ANCHOR is authoritative ..."
#     ... route-specific extra with full DATE_ANCHOR rules ...
#     return f"You are TodAI.\n{SPECIALIST_JSON_CONTRACT_LEGACY}\nRules:\n{extra}"


def _build_specialist_user_payload(
    *,
    route: str,
    server_snapshot: dict[str, Any],
    date_anchor: dict[str, Any] | None,
    highlights: dict[str, Any] | None,
    calendar_data: list[dict[str, Any]],
    goals_data: dict[str, Any] | None,
    preview_range: dict[str, Any] | None = None,
    current_message: str = "",
    full_index: dict[str, Any] | None = None,
    last_agent_mode: str | None = None,
    read_results: list[dict[str, Any]] | None = None,
) -> str:
    from todai.agent.planner.prompt_bundles import build_specialist_user_payload_slim, build_turn_facts

    _ = highlights, goals_data  # legacy args kept for call sites; slim bundle uses read_results only
    read_results = list(read_results or calendar_data)
    facts = build_turn_facts(
        route=route,
        current_message=current_message,
        date_anchor=date_anchor,
        read_results=read_results,
        preview_range=preview_range,
        server_snapshot=server_snapshot,
        full_index=full_index,
        last_agent_mode=last_agent_mode,
    )
    return build_specialist_user_payload_slim(facts)


# Legacy specialist payload (backup):
# def _build_specialist_user_payload_legacy(...):
#     parts = [f"ROUTE: {route}", DATE_ANCHOR, PREVIEW_SCOPE, SERVER_TIME,
#              UPCOMING_SCHEDULE_HIGHLIGHTS, CALENDAR_DATA[:12000], GOALS_DATA, ...]


# ── 2. Groq HTTP + JSON parse ─────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^\s*```(?:json)?\s*", "", t, flags=re.I)
    return re.sub(r"\s*```\s*$", "", t).strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    for candidate in (_strip_fences(text), text.strip()):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    return None
    return None


def _messages_mention_json(messages: list[dict[str, str]]) -> bool:
    return any(re.search(r"\bjson\b", m.get("content") or "", re.I) for m in messages)


def _ensure_json_mode_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Groq json_object mode requires the word JSON in the prompt."""
    if _messages_mention_json(messages):
        return messages
    out = [dict(m) for m in messages]
    for i, m in enumerate(out):
        if m.get("role") == "system":
            out[i] = {
                "role": "system",
                "content": (m.get("content") or "").rstrip() + "\nOutput JSON only.",
            }
            return out
    return [{"role": "system", "content": "Output JSON only."}, *out]


def _coerce_specialist_plain_text(content: str) -> dict[str, Any] | None:
    """Last resort when the model returns prose instead of a JSON object (chat turns)."""
    text = (content or "").strip()
    if not text or "{" in text:
        return None
    return {"replyText": text, "operations": []}


def groq_chat_json(
    messages: list[dict[str, str]],
    *,
    phase: str,
    max_tokens: int | None = None,
    temperature: float = 0.15,
) -> dict[str, Any]:
    user_id = current_turn_user_id()
    messages = _ensure_json_mode_messages(messages)
    json_mode_fallback = False
    first_json_status: int | None = None

    def _rate_limited_reply(check: TurnAllowance) -> dict[str, Any]:
        usage = groq_tracker.usage_snapshot(user_id)
        usage.update(check.to_usage_extra())
        msg = rate_limit_user_message(check, usage)
        return {
            "replyText": msg,
            "operations": [],
            "_groq_debug": {"ok": False, "phase": phase, "rate_limited": True, "limit_hit": check.limit_hit},
            "_api_usage": usage,
        }

    def _post(use_json_object: bool) -> tuple[int | None, dict[str, Any] | None, str | None]:
        gate = groq_tracker.check_single_request()
        if not gate.allowed:
            groq_tracker.record(user_id, phase=phase, status=429, ok=False, skipped=True)
            return 429, None, ("local_cap", gate)

        payload: dict[str, Any] = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if use_json_object:
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=90.0) as client:
                r = client.post(
                    f"{GROQ_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json=payload,
                )
                ok = r.status_code < 400
                data = r.json() if ok else None
                tokens = 0
                if isinstance(data, dict):
                    usage = data.get("usage") or {}
                    tokens = int(usage.get("total_tokens") or 0)
                groq_tracker.record(user_id, phase=phase, status=r.status_code, ok=ok, tokens=tokens)
                if r.status_code == 429:
                    retry = 60.0
                    ra = r.headers.get("retry-after") or r.headers.get("Retry-After")
                    if ra:
                        try:
                            retry = float(ra)
                        except ValueError:
                            pass
                    groq_tracker.set_external_retry(retry, "rpm")
                if r.status_code >= 400:
                    return r.status_code, None, r.text[:800]
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            return r.status_code, data, content
        except httpx.HTTPError as e:
            logger.warning("Groq HTTP error phase=%s: %s", phase, e)
            groq_tracker.record(user_id, phase=phase, status=None, ok=False)
            return None, None, str(e)

    status, data, content = _post(True)
    first_json_status = status
    if status == 429 and isinstance(content, tuple) and content[0] == "local_cap":
        return _rate_limited_reply(content[1])
    if status == 400:
        json_mode_fallback = True
        status, data, content = _post(False)
        if status == 429 and isinstance(content, tuple) and content[0] == "local_cap":
            return _rate_limited_reply(content[1])

    def _debug_base(ok: bool, **extra: Any) -> dict[str, Any]:
        dbg: dict[str, Any] = {
            "ok": ok,
            "phase": phase,
            "json_mode_fallback": json_mode_fallback,
        }
        if first_json_status is not None:
            dbg["first_json_status"] = first_json_status
        dbg.update(extra)
        return dbg

    if status is not None and status >= 400:
        if status == 429:
            snap = groq_tracker.usage_snapshot(user_id)
            wait = int(min(snap.get("retry_after_seconds") or 60, 60))
            return {
                "replyText": f"Groq rate limit (429). Wait about {wait}s and try again.",
                "operations": [],
                "_groq_debug": _debug_base(False, http_status=status),
                "_api_usage": snap,
            }
        return {
            "replyText": f"Groq HTTP {status}.",
            "operations": [],
            "_groq_debug": _debug_base(False, http_status=status),
        }
    if data is None:
        return {
            "replyText": "Groq network error; try again.",
            "operations": [],
            "_groq_debug": _debug_base(False),
        }

    parsed = _extract_json(content or "")
    if isinstance(parsed, dict):
        parsed["_groq_debug"] = _debug_base(True)
        return parsed
    if phase == "specialist":
        coerced = _coerce_specialist_plain_text(content or "")
        if coerced is not None:
            coerced["_groq_debug"] = _debug_base(True, coerced_plain_text=True)
            return coerced
    preview = (content or "")[:900]
    return {
        "replyText": "Groq returned invalid JSON; try again.",
        "operations": [],
        "_groq_debug": _debug_base(False, raw_preview=preview),
    }


# ── 3–5. Router, specialist, mocks ────────────────────────────────────────


def mock_route(message: str) -> dict[str, Any]:
    from todai.agent.routing.preview_range import (
        PreviewReadKind,
        classify_preview_read,
        infer_time_scope_from_message,
    )

    m = message.lower().strip()
    today_d = date(2026, 5, 19)
    if any(w in m for w in ("delete", "remove", "clear")):
        route = AgentRoute.SCHEDULE_DELETE.value
    elif any(w in m for w in ("add", "book", "create", "move ", "reschedule")):
        route = AgentRoute.SCHEDULE_WRITE.value
    elif any(
        w in m
        for w in (
            "preview",
            "what's on",
            "whats on",
            "show my",
            "show schedule",
            "my week",
            "this week",
            "upcoming",
            "coming sch",
            "my sch",
        )
    ):
        route = AgentRoute.SCHEDULE_PREVIEW.value
    elif re.search(r"\bsch[ae]?du\w*\b", m) and len(m) > 12:
        route = AgentRoute.SCHEDULE_PREVIEW.value
    else:
        route = AgentRoute.CHAT.value
    time_scope = infer_time_scope_from_message(message)
    tools: list[dict[str, Any]] = []
    if route != AgentRoute.CHAT.value:
        kind = classify_preview_read(message)
        if kind == PreviewReadKind.FREE_DAYS:
            tools = [
                {"tool": "get_days_without_schedule"},
                {"tool": "get_schedule_range"},
            ]
        elif kind == PreviewReadKind.FREE_TIME:
            tools = [
                {"tool": "get_free_time"},
                {"tool": "get_schedule_range"},
            ]
        else:
            tools = [{"tool": "get_schedule_range"}]
    return {
        "route": route,
        "time_scope": time_scope,
        "tools": tools,
        "_groq_debug": {"ok": True, "mock": True},
    }


def route_turn(
    *,
    current_message: str,
    routing_context: list[dict[str, str]] | None = None,
    server_snapshot: dict[str, Any],
    conversation: dict[str, Any],
    date_anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not GROQ_API_KEY:
        return mock_route(current_message or "")
    ctx = _build_router_user_context(
        current_message=current_message,
        server_snapshot=server_snapshot,
        conversation=conversation,
        date_anchor=date_anchor,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": ROUTER_SYSTEM}]
    if routing_context:
        messages.extend(routing_context)
    messages.append({"role": "user", "content": ctx})
    out = groq_chat_json(messages, phase="router", max_tokens=120, temperature=0)
    if isinstance(out.get("_groq_debug"), dict):
        out["_groq_debug"]["prompt_bundle"] = "slim_v1"
        out["_groq_debug"]["prompt_chars"] = {
            "system": len(ROUTER_SYSTEM),
            "routing_context": sum(len(m.get("content") or "") for m in (routing_context or [])),
            "user_ctx": len(ctx),
        }
    return out


def default_tools_for_route(route: AgentRoute, full_index: dict[str, Any]) -> list[dict[str, Any]]:
    if route == AgentRoute.CHAT:
        return []
    return [{"tool": "get_schedule_range", "arguments": {}}]


def specialist_turn(
    *,
    route: AgentRoute,
    history: list[dict[str, str]],
    server_snapshot: dict[str, Any],
    date_anchor: dict[str, Any] | None = None,
    highlights: dict[str, Any] | None = None,
    read_results: list[dict[str, Any]],
    preview_range: dict[str, Any] | None = None,
    current_message: str = "",
    full_index: dict[str, Any] | None = None,
    last_agent_mode: str | None = None,
) -> dict[str, Any]:
    if not GROQ_API_KEY:
        return {
            "replyText": "Hi! I can help with your calendar (mock mode).",
            "operations": [],
            "_groq_debug": {"ok": True, "mock": True},
        }
    system = _build_specialist_system(route.value)
    payload = _build_specialist_user_payload(
        route=route.value,
        server_snapshot=server_snapshot,
        date_anchor=date_anchor,
        highlights=highlights,
        calendar_data=[],
        goals_data=None,
        preview_range=preview_range,
        current_message=current_message,
        full_index=full_index,
        last_agent_mode=last_agent_mode,
        read_results=read_results,
    )
    hist_chars = sum(len(m.get("content") or "") for m in history)
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": payload},
    ]
    out = groq_chat_json(messages, phase="specialist")
    dbg = out.get("_groq_debug") if isinstance(out, dict) else None
    if isinstance(dbg, dict):
        dbg["prompt_bundle"] = "slim_v1"
        dbg["prompt_chars"] = {
            "system": len(system),
            "history": hist_chars,
            "turn_facts": len(payload),
            "total_user": hist_chars + len(payload),
        }
    return out


def parse_specialist_output(raw: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    reply = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
    ops = raw.get("operations")
    operations = ops if isinstance(ops, list) else []
    if operations and reply_is_clarifying(reply):
        return reply, []
    return reply, operations


def run_router(
    *,
    current_message: str,
    history: list[dict[str, str]],
    server_snapshot: dict[str, Any],
    conversation: dict[str, Any],
    date_anchor: dict[str, Any] | None = None,
) -> tuple[RouterOutput | None, list[dict[str, Any]], dict[str, Any] | None]:
    from todai.agent.core.context import groq_router_context

    routing_context = groq_router_context(history, current_message)
    raw = route_turn(
        current_message=current_message,
        routing_context=routing_context or None,
        server_snapshot=server_snapshot,
        conversation=conversation,
        date_anchor=date_anchor,
    )
    router_dbg = raw.pop("_groq_debug", None) if isinstance(raw, dict) else None
    out, errs = parse_router_output(raw if isinstance(raw, dict) else {})
    if out is None:
        fallback = mock_route(current_message)
        fb_dbg = fallback.pop("_groq_debug", None)
        out, fb_errs = parse_router_output(fallback)
        errs = [*errs, *fb_errs]
        if isinstance(router_dbg, dict):
            router_dbg = {**router_dbg, "rules_fallback": True, "fallback_ok": bool(out)}
        else:
            router_dbg = {"rules_fallback": True, "fallback_ok": bool(out), **(fb_dbg or {})}
    return out, errs, router_dbg
