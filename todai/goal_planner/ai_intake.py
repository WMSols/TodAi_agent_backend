"""AI-driven goal intake for the New goal tab (title + description → tailored questions → plan)."""

from __future__ import annotations

import json
import logging
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.interrogation import (
    confirmation_prompt,
    parse_confirmation,
    try_apply_confirm_edits,
)

logger = logging.getLogger(__name__)

_INIT_SYSTEM = (
    "You are TodAI goal intake. Given a goal TITLE and DESCRIPTION, output JSON only.\n"
    '{"analysis": string, "questions": [{"id": string, "text": string}], '
    '"defaults": {"objective": string, "difficulty": "easy|medium|hard", '
    '"tasks_per_day": 1-5, "minutes_per_day": number}}\n'
    "Context: read title and description first. Ask 3-5 tailored questions, not generic forms.\n"
    "Each question text: two sentences, max 25 words. Cover outcome, schedule, daily capacity.\n"
    "analysis: two sentences, max 25 words, show you understood their goal.\n"
    "defaults: infer tasks_per_day and minutes from their words (e.g. 1 hour → 60 minutes).\n"
)

_FINALIZE_SYSTEM = (
    "Map goal intake Q&A into plan parameters. JSON only.\n"
    '{"objective": string, "difficulty": "easy|medium|hard", '
    '"tasks_per_day": 1-5, "minutes_per_day": number}\n'
    "Honor explicit user numbers. Objective = specific 7-day outcome for their goal domain. "
    "Tasks will be generated day-by-day toward that objective."
)

_FALLBACK_QUESTIONS = [
    {
        "id": "outcome",
        "text": (
            "**Question 1** — What specific outcome do you want in the next 7 days "
            "(measurable if possible)?"
        ),
    },
    {
        "id": "schedule",
        "text": (
            "**Question 2** — How many days per week can you work on this, and "
            "roughly how many minutes per day?"
        ),
    },
    {
        "id": "intensity",
        "text": (
            "**Question 3** — How intense should this week feel: **easy**, **medium**, or **hard**?"
        ),
    },
]


def init_ai_intake(title: str, description: str) -> tuple[str, dict[str, Any]]:
    """Analyze title/description and return first assistant message + session patch."""
    title = (title or "").strip()
    desc = (description or "").strip()
    payload = {"title": title, "description": desc}
    analysis = ""
    questions = list(_FALLBACK_QUESTIONS)
    defaults = {
        "objective": title or desc or "7-day goal",
        "difficulty": "medium",
        "tasks_per_day": 1,
        "minutes_per_day": 45,
    }

    if GROQ_API_KEY:
        try:
            raw = groq_chat_json(
                [
                    {"role": "system", "content": _INIT_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                phase="goal_intake_init",
                max_tokens=700,
                temperature=0.3,
            )
            parsed = _parse_init(raw)
            if parsed:
                analysis = parsed.get("analysis") or analysis
                if parsed.get("questions"):
                    questions = parsed["questions"][:6]
                if parsed.get("defaults"):
                    defaults = {**defaults, **parsed["defaults"]}
        except Exception as e:
            logger.warning("goal intake init Groq failed: %s", e)

    if not analysis:
        analysis = (
            f"I'll help you build a **7-day plan** for **{title or 'your goal'}**. "
            "Answer a few quick questions so I can schedule realistic daily tasks."
        )

    ai_intake = {
        "analysis": analysis,
        "questions": questions,
        "answers": {},
        "index": 0,
        "defaults": defaults,
    }
    first_q = questions[0]["text"] if questions else "What do you want to achieve this week?"
    reply = f"{analysis}\n\n{first_q}"
    patch = {
        "phase": "interrogate",
        "intake_style": "ai",
        "ai_intake": ai_intake,
        "answers": {},
        "title": title,
        "description": desc,
    }
    return reply, patch


def handle_ai_intake_turn(session: dict[str, Any], message: str) -> tuple[str, dict[str, Any]]:
    """Advance AI intake: store answer, ask next question, or move to confirm."""
    ai = session.get("ai_intake") or {}
    questions: list[dict[str, Any]] = ai.get("questions") or []
    answers_map: dict[str, str] = dict(ai.get("answers") or {})
    idx = int(ai.get("index") or 0)
    text = (message or "").strip()

    if not questions:
        return init_ai_intake(session.get("title", ""), session.get("description", ""))

    if idx < len(questions) and text:
        qid = str(questions[idx].get("id") or f"q{idx}")
        answers_map[qid] = text
        idx += 1
        ai["answers"] = answers_map
        ai["index"] = idx

    if idx < len(questions):
        next_q = questions[idx].get("text") or "Next question:"
        reply = f"Got it.\n\n{next_q}"
        return reply, {"phase": "interrogate", "ai_intake": ai}

    structured = finalize_ai_answers(session)
    session["answers"] = structured
    session["phase"] = "confirm"
    return confirmation_prompt(structured), {"phase": "confirm", "answers": structured, "ai_intake": ai}


def handle_ai_confirm(session: dict[str, Any], message: str) -> tuple[str, dict[str, Any]]:
    """Confirm step for AI intake (yes → ready, no → restart, inline edits applied)."""
    answers = dict(session.get("answers") or {})
    title = (session.get("title") or "").strip()
    desc = (session.get("description") or "").strip()
    default_obj = title or desc

    answers, ack = try_apply_confirm_edits(message, answers, default_objective=default_obj)
    choice = parse_confirmation(message)

    if ack:
        patch: dict[str, Any] = {"phase": "confirm", "answers": answers}
        if choice == "yes":
            return "", {**patch, "phase": "ready"}
        return (
            f"Updated — {ack}\n\n{confirmation_prompt(answers)}\n\n"
            "Reply **yes** to build your 7-day task plan.",
            patch,
        )

    if choice == "yes":
        return "", {"phase": "ready", "answers": answers}
    if choice == "no":
        ai = session.get("ai_intake") or {}
        ai["index"] = 0
        ai["answers"] = {}
        first = (ai.get("questions") or [{}])[0].get("text", "Let's try again.")
        return (
            "No problem — let's adjust.\n\n" + first,
            {"phase": "interrogate", "ai_intake": ai, "answers": {}},
        )
    return (
        f"{confirmation_prompt(answers)}\n\n"
        "Reply **yes** to build, or correct a setting (e.g. **1 task per day**, **60 minutes**).",
        {"phase": "confirm", "answers": answers},
    )


def finalize_ai_answers(session: dict[str, Any]) -> dict[str, Any]:
    """Convert AI Q&A + defaults into validated answers dict for task generation."""
    ai = session.get("ai_intake") or {}
    defaults = dict(ai.get("defaults") or {})
    title = (session.get("title") or "").strip()
    desc = (session.get("description") or "").strip()
    qa_lines = [
        f"Q: {q.get('text', '')}\nA: {ai.get('answers', {}).get(str(q.get('id') or ''), '')}"
        for q in (ai.get("questions") or [])
    ]

    params = dict(defaults)
    if GROQ_API_KEY and qa_lines:
        try:
            raw = groq_chat_json(
                [
                    {"role": "system", "content": _FINALIZE_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "title": title,
                                "description": desc,
                                "analysis": ai.get("analysis"),
                                "qa": qa_lines,
                                "defaults": defaults,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                phase="goal_intake_finalize",
                max_tokens=200,
                temperature=0,
            )
            if isinstance(raw, dict):
                params.update(
                    {k: raw[k] for k in ("objective", "difficulty", "tasks_per_day", "minutes_per_day") if k in raw}
                )
        except Exception as e:
            logger.warning("goal intake finalize Groq failed: %s", e)

    return _answers_from_params(params, title=title, desc=desc)


def _answers_from_params(params: dict[str, Any], *, title: str, desc: str) -> dict[str, Any]:
    obj = str(params.get("objective") or title or desc or "7-day goal")[:500]
    diff = str(params.get("difficulty") or "medium").lower()
    if diff not in ("easy", "medium", "hard"):
        diff = "medium"
    try:
        tpd = int(params.get("tasks_per_day") or 1)
    except (TypeError, ValueError):
        tpd = 1
    tpd = max(1, min(5, tpd))
    try:
        mins = int(params.get("minutes_per_day") or 45)
    except (TypeError, ValueError):
        mins = 45
    mins = max(5, min(480, mins))

    out: dict[str, Any] = {}
    for step, val, display in (
        ("objective", obj, obj[:80]),
        ("difficulty", diff, diff),
        ("tasks_per_day", tpd, str(tpd)),
        ("minutes_per_day", mins, f"{mins} minutes per day"),
    ):
        out[step] = {"valid": True, "parsed": val, "raw": str(val), "display": display}
    return out


def _parse_init(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    questions = raw.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    cleaned = []
    for i, q in enumerate(questions):
        if isinstance(q, dict) and q.get("text"):
            cleaned.append({"id": str(q.get("id") or f"q{i + 1}"), "text": str(q["text"]).strip()})
        elif isinstance(q, str) and q.strip():
            cleaned.append({"id": f"q{i + 1}", "text": q.strip()})
    if not cleaned:
        return None
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    return {
        "analysis": str(raw.get("analysis") or "").strip(),
        "questions": cleaned,
        "defaults": defaults,
    }


def uses_ai_intake(session: dict[str, Any], ui_mode: str) -> bool:
    return ui_mode == "new_goal" and session.get("intake_style") == "ai"
