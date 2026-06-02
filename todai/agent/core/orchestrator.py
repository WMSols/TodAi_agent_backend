"""
main.py — orchestrate one user message

Flow:
  1. Append user message, set FSM to analyzing
  2. router.run_router → intent label + optional read tools
  3. prefetch.resolve_and_prefetch → calendar/goals data
  4. intents.dispatch → per-intent handler (chat / preview / write / delete)
  5. Save assistant message + return ChatResponse
"""

from __future__ import annotations

import uuid
from typing import Any

from todai.agent.planner.llm import AgentRoute
from todai.agent.core.context import (
    assistant_meta,
    groq_history_from_chat,
    merged_write_context_message,
    groq_specialist_history,
)
from todai.agent.routing.time_scope import normalize_time_scope, resolve_preview_range_for_turn
from todai.agent.routing.date_anchor import build_date_anchor
from todai.agent.core.intents import dispatch
from todai.agent.core.prefetch import resolve_and_prefetch
from todai.agent.routing.router import run_router
from todai.agent.routing.routing_guards import apply_route_guards
from todai.agent.core.types import (
    AgentMode,
    ConversationState,
    TurnContext,
    chat_response_from_turn,
    route_to_agent_mode,
)
from todai.api.middleware.rate_limit import groq_tracker, rate_limit_user_message
from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.api.logging import logger
from todai.database.models import ChatResponse
from todai.database.stores import UserStore


def orchestrate_turn(store: UserStore, *, user_id: str, message: str) -> ChatResponse:
    groq_tracker.begin_turn(user_id)
    chat = store.read_chat()
    trace: list[dict[str, Any]] = []

    if GROQ_API_KEY:
        gate = groq_tracker.check_turn_allowed()
        if not gate.allowed:
            usage = groq_tracker.usage_snapshot(user_id)
            usage.update(gate.to_usage_extra())
            trace.append({"phase": "rate_limit", "limit_hit": gate.limit_hit, "wait": usage["retry_after_seconds"]})
            reply = rate_limit_user_message(gate, usage)
            chat["messages"].append({"role": "user", "content": message})
            chat["messages"].append({"role": "assistant", "content": reply})
            store.write_chat(chat)
            return _rate_limit_response(reply, chat, trace, usage, user_id=user_id)

    chat["messages"].append({"role": "user", "content": message})
    full_index = store.planner_storage_index()
    date_anchor = build_date_anchor(full_index, message=message)
    trace.append(
        {
            "phase": "date_anchor",
            "today": date_anchor.get("today", {}).get("iso"),
            "mentioned_weekdays": date_anchor.get("mentioned_weekdays"),
            "weekday_candidates": date_anchor.get("weekday_candidates"),
        }
    )
    server_snapshot = {
        "server_date_utc": full_index.get("server_date_utc"),
        "server_datetime_utc": full_index.get("server_datetime_utc"),
        "user_id": full_index.get("user_id"),
        "calendar_files": full_index.get("calendar_files"),
        "known_block_ids": full_index.get("known_block_ids"),
    }
    conversation = {
        "fsm_state": chat.get("state", "idle"),
        "schedule_version": chat.get("schedule_version"),
        "last_agent_mode": chat.get("last_agent_mode"),
    }

    chat["state"] = ConversationState.ANALYZING.value
    chat["last_turn_id"] = str(uuid.uuid4())
    store.write_chat(chat)

    router_history = groq_history_from_chat(chat["messages"])
    router_out, router_errs, router_dbg = run_router(
        current_message=message,
        history=router_history,
        server_snapshot=server_snapshot,
        conversation=conversation,
        date_anchor=date_anchor,
    )

    if router_out is None:
        reply = "I had trouble routing that message — try again."
        chat["state"] = ConversationState.ERROR.value
        chat["messages"].append({"role": "assistant", "content": reply, "meta": {"errors": router_errs}})
        store.write_chat(chat)
        return _error_response(reply, chat, AgentMode.CHAT, trace, router_errs, router_dbg, user_id=user_id)

    route, tools, guard_notes = apply_route_guards(
        message,
        router_out,
        last_agent_mode=conversation.get("last_agent_mode"),
    )
    for note in guard_notes:
        trace.append(note)
    router_out.route = route.value
    router_out.tools = tools
    mode = route_to_agent_mode(route)
    trace.append(
        {
            "phase": "router",
            "route": route.value,
            "time_scope": router_out.time_scope,
            "tools": router_out.tools,
        }
    )
    if router_dbg and isinstance(router_dbg, dict) and router_dbg.get("prompt_chars"):
        trace.append(
            {
                "phase": "prompt_bundle",
                "target": "router",
                "bundle": router_dbg.get("prompt_bundle"),
                "chars": router_dbg.get("prompt_chars"),
            }
        )

    if route != AgentRoute.CHAT or router_out.tools:
        chat["state"] = ConversationState.REQUESTING_DATA.value
        store.write_chat(chat)

    ts = normalize_time_scope(router_out.time_scope)
    preview_range = None
    if route != AgentRoute.CHAT:
        preview_range = resolve_preview_range_for_turn(
            time_scope=ts,
            message=message,
            date_anchor=date_anchor,
            full_index=full_index,
            route=route.value,
        )

    tool_calls, read_results, prefetch_errors, preview_range = resolve_and_prefetch(
        store,
        route=route,
        router_tools=router_out.tools,
        full_index=full_index,
        server_today=server_snapshot.get("server_date_utc"),
        message=message,
        date_anchor=date_anchor,
        preview_range=preview_range,
        time_scope=ts,
    )
    trace.append({"phase": "prefetch", "calls": tool_calls, "errors": prefetch_errors})
    if preview_range and route != AgentRoute.CHAT:
        trace.append({"phase": "time_scope", **preview_range.as_dict()})
    if route == AgentRoute.SCHEDULE_PREVIEW:
        from todai.agent.routing.preview_read_kind import classify_preview_read

        trace.append({"phase": "preview_read_kind", "kind": classify_preview_read(message).value})

    if prefetch_errors and not read_results and route != AgentRoute.CHAT:
        chat["state"] = ConversationState.ERROR.value
        reply = "A data request failed."
        chat["messages"].append({"role": "assistant", "content": reply, "meta": {"errors": prefetch_errors}})
        store.write_chat(chat)
        return _error_response(reply, chat, mode, trace, prefetch_errors, router_dbg, user_id=user_id)

    specialist_history = groq_specialist_history(chat["messages"], route.value)
    specialist_message = message
    if route == AgentRoute.SCHEDULE_WRITE:
        specialist_message = merged_write_context_message(chat["messages"], message)

    ctx = TurnContext(
        store=store,
        user_id=user_id,
        message=specialist_message,
        chat=chat,
        history=specialist_history,
        route=route,
        server_snapshot=server_snapshot,
        conversation=conversation,
        full_index=full_index,
        read_results=read_results,
        date_anchor=date_anchor,
        highlights=None,
        trace=trace,
        router_dbg=router_dbg,
        preview_range=preview_range,
    )

    result = dispatch(ctx)

    if result.months_written and not result.apply_errors:
        chat["schedule_version"] = int(chat.get("schedule_version", 1)) + 1

    chat["last_agent_mode"] = mode.value
    chat["pending_proposal"] = None
    chat["pending_proposal_id"] = None
    chat["state"] = ConversationState.IDLE.value
    chat["messages"].append(
        {
            "role": "assistant",
            "content": result.reply_text,
            "meta": assistant_meta(ctx.trace, result.schedule_display, route=route.value),
        }
    )
    store.write_chat(chat)

    logger.info(
        "orchestrate_turn user=%s intent=%s ops=%d months=%d",
        user_id,
        route.value,
        len(result.operations),
        result.months_written,
    )

    return chat_response_from_turn(ctx, result, mode=mode, user_id=user_id)


def _rate_limit_response(
    reply: str,
    chat: dict[str, Any],
    trace: list[dict[str, Any]],
    usage: dict[str, Any],
    *,
    user_id: str,
) -> ChatResponse:
    from todai.agent.planner.groq_config import planner_mode

    return ChatResponse(
        assistant_text=reply,
        reply_text=reply,
        state=str(chat.get("state", "idle")),
        schedule_version=int(chat.get("schedule_version", 1)),
        agent_mode=chat.get("last_agent_mode") or "chat",
        agent_state=chat.get("last_agent_mode") or "chat",
        tool_trace=trace,
        validator_errors=[],
        debug={"pipeline": "orchestrator", "planner": planner_mode(), "api_usage": usage},
        api_usage=usage,
    )


def _error_response(
    reply: str,
    chat: dict[str, Any],
    mode: AgentMode,
    trace: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    router_dbg: dict[str, Any] | None,
    *,
    user_id: str,
) -> ChatResponse:
    from todai.agent.planner.groq_config import planner_mode

    usage = groq_tracker.usage_snapshot(user_id)
    dbg: dict[str, Any] = {"pipeline": "orchestrator", "planner": planner_mode(), "api_usage": usage}
    if router_dbg:
        dbg["router_groq"] = router_dbg
    return ChatResponse(
        assistant_text=reply,
        reply_text=reply,
        state=str(chat.get("state", "idle")),
        schedule_version=int(chat.get("schedule_version", 1)),
        agent_mode=mode.value,
        agent_state=mode.value,
        tool_trace=trace,
        validator_errors=errors,
        debug=dbg,
        api_usage=usage,
    )
