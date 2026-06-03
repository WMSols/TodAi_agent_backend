"""Goal chat specialist — conversational replies on active plans (Groq + fallback)."""

from __future__ import annotations

import json
import logging
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.interrogation import is_active_acknowledgment
from todai.goal_planner.plan_state import plan_needs_task_setup
from todai.goal_planner.routing.context import groq_goal_chat_context
from todai.goal_planner.session_store import GoalPlanSessionStore

logger = logging.getLogger(__name__)

_CHAT_SYSTEM = (
    "You are TodAI, a friendly goal-planning coach. The user has a 7-day goal week plan.\n"
    "Reply with JSON only: {\"replyText\": string}\n"
    "Be natural and concise (1-4 sentences). Answer greetings and small talk warmly.\n"
    "Use PLAN_SNAPSHOT for context. Suggest concrete next steps when helpful:\n"
    "- **show my plan** — tasks for this week\n"
    "- **my schedule** — calendar + goal tasks\n"
    "- **review goals** — all goals and progress\n"
    "- If PLAN_SNAPSHOT.needs_setup is true, guide them through the 4 setup questions in this chat "
    "(difficulty, tasks/day, minutes) — do not tell them to switch tabs.\n"
    "Do NOT perform deletes or claim tasks were changed; tell them to ask to delete or edit.\n"
    "No markdown code fences inside JSON."
)

_FALLBACK_HINT = (
    "I can help with your 7-day goal plan. Try **show my plan**, **my schedule**, "
    "or **review goals** — or open **New goal** to start another plan."
)


def _plan_snapshot(store: GoalPlanSessionStore, plan_id: str) -> dict[str, Any]:
    row = store.get_plan_row(plan_id) or {}
    tasks = store.list_goal_tasks(plan_id) if plan_id else []
    done = sum(1 for t in tasks if (t.get("status") or "").lower() in ("done", "completed"))
    total = len(tasks)
    objective = ""
    answers = (store._load_plan_session(plan_id) or {}).get("answers") or {}
    if answers.get("objective", {}).get("parsed"):
        objective = str(answers["objective"]["parsed"])
    elif row.get("plan_notes"):
        objective = str(row.get("plan_notes") or "")

    goals = store.list_user_goals()
    goal_title = ""
    gid = str(row.get("goal_id") or "")
    for g in goals:
        if str(g.get("id")) == gid:
            goal_title = str(g.get("title") or "")
            break

    sess = store._load_plan_session(plan_id) or {}
    needs_setup = plan_needs_task_setup(store, plan_id, sess)

    return {
        "plan_id": plan_id,
        "goal_title": goal_title,
        "goal_description": next(
            (str(g.get("description") or "") for g in goals if str(g.get("id")) == gid),
            "",
        ),
        "objective": objective,
        "needs_setup": needs_setup,
        "status": row.get("status"),
        "start_date": str(row.get("start_date") or "")[:10],
        "end_date": str(row.get("end_date") or "")[:10],
        "difficulty": row.get("difficulty"),
        "progress": {
            "done": done,
            "total": total,
            "percent": int((done / total) * 100) if total else 0,
        },
    }


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
            "I'm here for the **4 setup questions** on this tab. "
            "For open conversation, switch to **My goals** — or answer the question above.",
            {},
            trace,
        )

    if is_active_acknowledgment(message):
        return (
            "You're welcome! Ask **show my plan** anytime to see this week's tasks.",
            {},
            trace,
        )

    snapshot = _plan_snapshot(store, plan_id)
    reply = _groq_chat_reply(message, history, snapshot)
    if reply:
        trace.append({"phase": "goal_chat", "source": "groq"})
        return reply, {}, trace

    trace.append({"phase": "goal_chat", "source": "fallback"})
    return _fallback_reply(snapshot, message), {}, trace


def _groq_chat_reply(
    message: str,
    history: list[dict[str, Any]] | None,
    snapshot: dict[str, Any],
) -> str | None:
    if not GROQ_API_KEY:
        return None
    ctx = groq_goal_chat_context(history or [])
    payload = json.dumps(
        {"user_message": message, "PLAN_SNAPSHOT": snapshot},
        ensure_ascii=False,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _CHAT_SYSTEM},
        *ctx,
        {"role": "user", "content": payload},
    ]
    try:
        raw = groq_chat_json(messages, phase="goal_chat", max_tokens=350, temperature=0.45)
        text = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
        if not text:
            return None
        low = text.lower()
        if low.startswith("groq network error") or low.startswith("groq http") or "rate limit" in low:
            return None
        return text
    except Exception as e:
        logger.warning("goal chat Groq failed: %s", e)
        return None


def _fallback_reply(snapshot: dict[str, Any], message: str) -> str:
    obj = (snapshot.get("objective") or snapshot.get("goal_title") or "your goal").strip()
    prog = snapshot.get("progress") or {}
    if prog.get("total"):
        return (
            f"Your plan for **{obj[:60]}** is active "
            f"({prog.get('done', 0)}/{prog.get('total', 0)} tasks done). "
            f"{_FALLBACK_HINT}"
        )
    return _FALLBACK_HINT
