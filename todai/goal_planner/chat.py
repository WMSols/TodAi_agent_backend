"""Goal chat specialist — conversational replies on active plans (Groq + grounded plan context)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from todai.agent.core.groq_errors import is_groq_failure_reply
from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.interrogation import is_active_acknowledgment
from todai.goal_planner.plan_context import build_goal_plan_context
from todai.goal_planner.routing import groq_goal_chat_context
from todai.goal_planner.session_store import GoalPlanSessionStore

logger = logging.getLogger(__name__)

_CHAT_SYSTEM = (
    "You are TodAI, a friendly goal-planning coach. The user has a 7-day goal week plan.\n"
    "Reply with JSON only: {\"replyText\": string}\n\n"
    "You receive PLAN_DATA (server-grounded facts). Use it for every factual answer.\n"
    "Rules:\n"
    "- Answer the user's **question** directly (advice, hardest days, progress, what to focus on, how-to).\n"
    "- Use **plan_day** (1..N), **weekday**, and **date** from PLAN_DATA.days — never invent day numbers.\n"
    "- Only mention tasks that appear in PLAN_DATA.tasks_by_date. Never invent tasks.\n"
    "- For hardest/challenging days use PLAN_DATA.hardest_days + intense_task titles.\n"
    "- Empty days (task_count=0) may be skip days — say so using skip_days_label when relevant.\n"
    "- Include brief encouragement and 1-2 practical tips when coaching.\n"
    "- For today's date use ONLY PLAN_DATA.server_today.\n"
    "- Warm markdown (**bold**, bullets). Usually 2-6 sentences; longer if listing days/tasks.\n"
    "- For a full task LIST use **show my plan** (task table is not your job — you coach).\n"
    "- Do NOT claim you deleted, moved, or completed tasks — tell them to ask explicitly.\n"
    "- If needs_setup is true, guide the 3 setup questions on this tab.\n"
    "No markdown code fences inside JSON."
)

_FALLBACK_HINT = (
    "I can help with your 7-day goal plan. Try **show my plan**, **my schedule**, "
    "or **review goals** — or open **New goal** to start another plan."
)


def handle_goal_chat(
    store: GoalPlanSessionStore,
    plan_id: str,
    message: str,
    *,
    session: dict[str, Any],
    phase: str,
    history: list[dict[str, Any]] | None = None,
    ui_mode: str = "my_goals",
    needs_task_setup: bool = False,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = [{"phase": "goal_chat", "plan_phase": phase, "ui_mode": ui_mode}]

    if needs_task_setup and ui_mode == "my_goals":
        return (
            "This plan doesn't have tasks yet. Go to the **New goal** tab — enter your title "
            "and description, click **Start plan**, then answer the AI's questions to build "
            "your 7-day schedule.",
            {},
            trace,
        )

    if ui_mode == "new_goal" and phase in ("interrogate", "confirm", "ready"):
        return (
            "I'm here for the **3 setup questions** on this tab. "
            "For open conversation, switch to **My goals** — or answer the question above.",
            {},
            trace,
        )

    if is_active_acknowledgment(message):
        return (
            "You're welcome! Ask me about your plan, progress, or hardest days — or say **show my plan**.",
            {},
            trace,
        )

    from todai.database.utils.dates import format_today_reply, is_today_question
    from todai.goal_planner.today_context import get_server_today_for_user

    if is_today_question(message):
        today = get_server_today_for_user(store.api_user_id)
        trace.append({"phase": "today_reply", "source": "server"})
        return format_today_reply(today), {}, trace

    plan_data = build_goal_plan_context(store, plan_id)
    trace.append(
        {
            "phase": "goal_plan_context",
            "task_count": plan_data.get("task_count"),
            "plan_days": plan_data.get("plan_days"),
        }
    )

    reply = _groq_chat_reply(message, history, plan_data)
    patch: dict[str, Any] = {}
    display = plan_data.get("schedule_display")
    if display and plan_data.get("task_count"):
        patch["schedule_display"] = display

    if reply:
        trace.append({"phase": "goal_chat", "source": "groq"})
        return reply, patch, trace

    trace.append({"phase": "goal_chat", "source": "fallback"})
    return _fallback_reply(plan_data, message), patch, trace


def _groq_chat_reply(
    message: str,
    history: list[dict[str, Any]] | None,
    plan_data: dict[str, Any],
) -> str | None:
    if not GROQ_API_KEY:
        return None
    ctx = groq_goal_chat_context(history or [])
    payload_data = {k: v for k, v in plan_data.items() if k != "schedule_display"}
    payload = json.dumps(
        {"user_message": message, "PLAN_DATA": payload_data},
        ensure_ascii=False,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _CHAT_SYSTEM},
        *ctx,
        {"role": "user", "content": payload},
    ]
    try:
        raw = groq_chat_json(messages, phase="goal_chat", max_tokens=600, temperature=0.4)
        text = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
        if not text or is_groq_failure_reply(text):
            return None
        return text
    except Exception as e:
        logger.warning("goal chat Groq failed: %s", e)
        return None


def _fallback_reply(plan_data: dict[str, Any], message: str) -> str:
    obj = (plan_data.get("objective") or plan_data.get("goal_title") or "your goal").strip()
    prog = plan_data.get("progress") or {}
    hardest = plan_data.get("hardest_days") or []
    low = (message or "").lower()
    if prog.get("total") and re.search(r"\b(hard|difficult|challenging|hardest|tough)\b", low):
        if hardest:
            labels = ", ".join(f"**{h['label']}** (day {h['plan_day']})" for h in hardest[:2])
            return (
                f"Your toughest plan days look like {labels}. "
                f"Progress: **{prog.get('done', 0)}/{prog.get('total', 0)}** tasks done. "
                f"Say **show my plan** for the full list."
            )
    if prog.get("total"):
        return (
            f"Your plan for **{obj[:60]}** is active "
            f"({prog.get('done', 0)}/{prog.get('total', 0)} tasks done). "
            f"{_FALLBACK_HINT}"
        )
    return _FALLBACK_HINT
