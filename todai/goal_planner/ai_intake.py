"""AI-driven goal intake: user describes what they want → AI sets goal title + asks questions."""

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
    "You are TodAI goal intake. The user describes what they want to achieve (no title yet).\n"
    "Output JSON only:\n"
    '{"goal_title": string, "user_notes": string, "analysis": string, '
    '"questions": [{"id": string, "text": string}], '
    '"defaults": {"objective": string, "difficulty": "easy|medium|hard", '
    '"tasks_per_day": 1-5, "minutes_per_day": number}}\n'
    "goal_title: short label for the goal (3-8 words), e.g. Learn Python basics.\n"
    "user_notes: preserve their instructions as plan notes (1-3 sentences).\n"
    "Ask 3-5 tailored questions from their words — not a generic form.\n"
    "Each question: two sentences, max 25 words. Cover outcome, schedule, daily capacity.\n"
    "analysis: two sentences, max 25 words — show you understood them.\n"
    "defaults.objective: specific 7-day outcome aligned with goal_title and user_notes.\n"
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


def init_ai_intake(
    achievement: str,
    description: str = "",
    *,
    allow_groq: bool = True,
) -> tuple[str, dict[str, Any]]:
    """
    User provides what they want to achieve; AI proposes goal_title and tailored questions.
    `description` is legacy alias — merged into achievement when empty achievement.
    """
    text = (achievement or "").strip() or (description or "").strip()
    return _init_from_achievement(text, allow_groq=allow_groq)


def _init_from_achievement(achievement: str, *, allow_groq: bool = True) -> tuple[str, dict[str, Any]]:
    achievement = (achievement or "").strip()
    payload = {"achievement": achievement}
    analysis = ""
    questions = list(_FALLBACK_QUESTIONS)
    goal_title = _fallback_title(achievement)
    user_notes = achievement
    defaults = {
        "objective": achievement or "7-day goal",
        "difficulty": "medium",
        "tasks_per_day": 1,
        "minutes_per_day": 45,
    }

    if GROQ_API_KEY and achievement and allow_groq:
        try:
            raw = groq_chat_json(
                [
                    {"role": "system", "content": _INIT_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                phase="goal_intake_init",
                max_tokens=750,
                temperature=0.3,
            )
            parsed = _parse_init(raw)
            if parsed:
                analysis = parsed.get("analysis") or analysis
                if parsed.get("questions"):
                    questions = parsed["questions"][:6]
                if parsed.get("defaults"):
                    defaults = {**defaults, **parsed["defaults"]}
                if parsed.get("goal_title"):
                    goal_title = str(parsed["goal_title"]).strip()[:200]
                if parsed.get("user_notes"):
                    user_notes = str(parsed["user_notes"]).strip()[:4000]
        except Exception as e:
            logger.warning("goal intake init Groq failed: %s", e)

    if not analysis:
        analysis = (
            f"I'll shape a **7-day plan** around your goal. "
            f"I'll call it **{goal_title}** — answer a few quick questions next."
        )

    ai_intake = {
        "analysis": analysis,
        "questions": questions,
        "answers": {},
        "index": 0,
        "defaults": defaults,
        "goal_title": goal_title,
        "user_notes": user_notes,
    }
    first_q = questions[0]["text"] if questions else "What do you want to achieve this week?"
    reply = f"{analysis}\n\n{first_q}"
    patch = {
        "phase": "interrogate",
        "intake_style": "ai",
        "ai_intake": ai_intake,
        "answers": {},
        "title": goal_title,
        "description": user_notes,
        "achievement": achievement,
        "generated_goal_title": goal_title,
    }
    return reply, patch


def _fallback_title(achievement: str) -> str:
    words = (achievement or "New goal").split()[:6]
    t = " ".join(words).strip()
    return (t[:80] + "…") if len(t) > 80 else (t or "New goal")


def handle_ai_intake_turn(session: dict[str, Any], message: str) -> tuple[str, dict[str, Any]]:
    """Advance AI intake: store answer, ask next question, or move to confirm."""
    ai = session.get("ai_intake") or {}
    questions: list[dict[str, Any]] = ai.get("questions") or []
    answers_map: dict[str, str] = dict(ai.get("answers") or {})
    idx = int(ai.get("index") or 0)
    text = (message or "").strip()

    if not questions:
        ach = session.get("achievement") or session.get("description") or ""
        return init_ai_intake(ach)

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
    title = (session.get("title") or ai.get("goal_title") or "your goal").strip()
    return (
        confirmation_prompt(structured, goal_title=title),
        {"phase": "confirm", "answers": structured, "ai_intake": ai},
    )


def handle_ai_confirm(session: dict[str, Any], message: str) -> tuple[str, dict[str, Any]]:
    """Confirm step for AI intake (yes → ready, no → restart, inline edits applied)."""
    answers = dict(session.get("answers") or {})
    ai = session.get("ai_intake") or {}
    default_obj = (
        (answers.get("objective") or {}).get("parsed")
        or session.get("achievement")
        or session.get("description")
        or session.get("title")
        or ""
    )
    if isinstance(default_obj, dict):
        default_obj = str(default_obj.get("parsed") or "")
    default_obj = str(default_obj).strip()
    goal_title = (session.get("title") or ai.get("goal_title") or "your goal").strip()

    answers, ack = try_apply_confirm_edits(message, answers, default_objective=default_obj)
    choice = parse_confirmation(message)

    if ack:
        patch: dict[str, Any] = {"phase": "confirm", "answers": answers}
        if choice == "yes":
            return "", {**patch, "phase": "ready"}
        return (
            f"Updated — {ack}\n\n{confirmation_prompt(answers, goal_title=goal_title)}\n\n"
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
        f"{confirmation_prompt(answers, goal_title=goal_title)}\n\n"
        "Reply **yes** to build, or correct a setting (e.g. **1 task per day**, **60 minutes**).",
        {"phase": "confirm", "answers": answers},
    )


def finalize_ai_answers(session: dict[str, Any]) -> dict[str, Any]:
    """Convert AI Q&A + defaults into validated answers dict for task generation."""
    ai = session.get("ai_intake") or {}
    defaults = dict(ai.get("defaults") or {})
    achievement = (session.get("achievement") or session.get("description") or "").strip()
    goal_title = (session.get("title") or ai.get("goal_title") or "").strip()
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
                                "goal_title": goal_title,
                                "achievement": achievement,
                                "user_notes": ai.get("user_notes") or achievement,
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

    return _answers_from_params(
        params,
        fallback_objective=achievement or goal_title or "7-day goal",
    )


def _answers_from_params(params: dict[str, Any], *, fallback_objective: str) -> dict[str, Any]:
    obj = str(params.get("objective") or fallback_objective)[:500]
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
        "goal_title": str(raw.get("goal_title") or "").strip(),
        "user_notes": str(raw.get("user_notes") or "").strip(),
        "analysis": str(raw.get("analysis") or "").strip(),
        "questions": cleaned,
        "defaults": defaults,
    }


def uses_ai_intake(session: dict[str, Any], ui_mode: str) -> bool:
    return ui_mode == "new_goal" and session.get("intake_style") == "ai"
