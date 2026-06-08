"""AI-driven goal intake: user describes what they want → AI sets goal title + asks questions."""

from __future__ import annotations

import json
import logging
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.interrogation import (
    confirmation_prompt,
    format_skip_days,
    parse_skip_days,
)
from todai.goal_planner.intake_validate import validate_intake_answer
from todai.goal_planner.turn_normalize import apply_confirm_edits, normalize_confirmation

logger = logging.getLogger(__name__)

_INIT_SYSTEM = (
    "Goal intake from achievement text. JSON: "
    '{"goal_title":string,"user_notes":string,"analysis":string,'
    '"clarification_question":{"id":string,"text":string},'
    '"defaults":{"objective":string,"difficulty":"easy|medium|hard","tasks_per_day":1-5}}\n'
    "goal_title 3-8 words. ONE clarification Q on measurable 7-day outcome (≤18 words). "
    "Do NOT ask tasks/day, skip days, or minutes — fixed Qs handle those."
)

_FINALIZE_SYSTEM = (
    "Map intake Q&A to plan params. JSON: "
    '{"objective":string,"difficulty":"easy|medium|hard","tasks_per_day":1-5}\n'
    "Use explicit user numbers from Q&A; do not override tasks_per_day the user already gave."
)

_FIXED_TAIL_QUESTIONS = [
    {
        "id": "tasks_per_day",
        "text": (
            "**Question 2 of 3 — Tasks per day**\n"
            "How many separate tasks on each **active** day? Reply **1** to **5**."
        ),
    },
    {
        "id": "skip_days",
        "text": (
            "**Question 3 of 3 — Skip days**\n"
            "Which weekdays should have **no tasks**? "
            "Name one or more (e.g. **Monday** or **Saturday and Sunday**) or say **none** for every day."
        ),
    },
]

_FALLBACK_CLARIFICATION = {
    "id": "outcome",
    "text": (
        "**Question 1 of 3 — Your goal**\n"
        "What specific outcome do you want in the next 7 days (measurable if possible)?"
    ),
}


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
    questions = _build_intake_questions([_FALLBACK_CLARIFICATION])
    goal_title = _fallback_title(achievement)
    user_notes = achievement
    defaults = {
        "objective": achievement or "7-day goal",
        "difficulty": "medium",
        "tasks_per_day": 1,
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
                    questions = _build_intake_questions(parsed["questions"])
                elif parsed.get("clarification_question"):
                    questions = _build_intake_questions([parsed["clarification_question"]])
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
        "parsed_answers": {},
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


def handle_ai_intake_turn(
    session: dict[str, Any],
    message: str,
    *,
    allow_groq: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Advance AI intake: validate answer, save only on ok, then next question or confirm."""
    ai = dict(session.get("ai_intake") or {})
    questions: list[dict[str, Any]] = ai.get("questions") or []
    answers_map: dict[str, str] = dict(ai.get("answers") or {})
    idx = int(ai.get("index") or 0)
    text = (message or "").strip()
    meta: dict[str, Any] = {}

    if not questions:
        ach = session.get("achievement") or session.get("description") or ""
        reply, patch = init_ai_intake(ach, allow_groq=allow_groq)
        return reply, patch, meta

    if idx < len(questions) and not text:
        qtext = str(questions[idx].get("text") or "").strip()
        hint = "Please send a short answer — I can't use an empty message."
        reply = f"{hint}\n\n{qtext}" if qtext else hint
        meta["validate_status"] = "aborted"
        meta["validate_source"] = "local"
        return reply, {"phase": "interrogate", "ai_intake": ai}, meta

    if idx < len(questions) and text:
        current_q = questions[idx]
        qtext = str(current_q.get("text") or "").strip()
        goal_title = (session.get("title") or ai.get("goal_title") or "").strip()
        default_obj = goal_title or (session.get("achievement") or session.get("description") or "")
        validation = validate_intake_answer(
            question=current_q,
            answer=text,
            goal_title=goal_title,
            default_objective=default_obj,
            allow_groq=allow_groq,
        )
        meta["validate_status"] = validation.status
        meta["validate_source"] = validation.source
        if validation.parsed_value is not None:
            meta["parsed_value"] = validation.parsed_value

        if validation.status == "aborted":
            reply = validation.reply_text
            if qtext and qtext not in reply:
                reply = f"{reply}\n\n{qtext}"
            return reply, {"phase": "interrogate", "ai_intake": ai}, meta

        qid = str(current_q.get("id") or f"q{idx}")
        answers_map[qid] = text
        parsed_answers: dict[str, Any] = dict(ai.get("parsed_answers") or {})
        if validation.parsed_value is not None:
            parsed_answers[qid] = validation.parsed_value
        ai["parsed_answers"] = parsed_answers
        idx += 1
        ai["answers"] = answers_map
        ai["index"] = idx

        if idx < len(questions):
            next_q = str(questions[idx].get("text") or "Next question:")
            reply = f"{validation.reply_text}\n\n{next_q}"
            return reply, {"phase": "interrogate", "ai_intake": ai}, meta

    if idx < len(questions):
        next_q = questions[idx].get("text") or "Next question:"
        return next_q, {"phase": "interrogate", "ai_intake": ai}, meta

    session_with_ai = {**session, "ai_intake": ai}
    structured = finalize_ai_answers(session_with_ai, allow_groq=allow_groq)
    session["answers"] = structured
    session["phase"] = "confirm"
    title = (session.get("title") or ai.get("goal_title") or "your goal").strip()
    meta["validate_status"] = "complete"
    return (
        confirmation_prompt(structured, goal_title=title),
        {"phase": "confirm", "answers": structured, "ai_intake": ai},
        meta,
    )


def handle_ai_confirm(
    session: dict[str, Any], message: str, *, allow_groq: bool = True
) -> tuple[str, dict[str, Any]]:
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

    edit = apply_confirm_edits(
        message,
        answers,
        goal_title=goal_title,
        default_objective=default_obj,
        allow_groq=allow_groq,
    )
    answers = edit.answers
    ack = edit.ack
    choice = normalize_confirmation(
        message,
        context={
            "prompt_type": "build_plan_confirm",
            "phase": "confirm",
            "goal_title": goal_title,
        },
        allow_groq=allow_groq,
    ).choice

    if ack:
        patch: dict[str, Any] = {"phase": "confirm", "answers": answers}
        # Setting edits always show preview first — never create on the same turn as an edit.
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
        ai["parsed_answers"] = {}
        first = (ai.get("questions") or [{}])[0].get("text", "Let's try again.")
        return (
            "No problem — let's adjust.\n\n" + first,
            {"phase": "interrogate", "ai_intake": ai, "answers": {}},
        )
    return (
        f"{confirmation_prompt(answers, goal_title=goal_title)}\n\n"
        "Reply **yes** to build, or describe changes in plain language (you can update several settings at once).",
        {"phase": "confirm", "answers": answers},
    )


def finalize_ai_answers(session: dict[str, Any], *, allow_groq: bool = True) -> dict[str, Any]:
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
    if GROQ_API_KEY and qa_lines and allow_groq:
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
                    {
                        k: raw[k]
                        for k in ("objective", "difficulty", "tasks_per_day")
                        if k in raw
                    }
                )
        except Exception as e:
            logger.warning("goal intake finalize Groq failed: %s", e)

    params.update(_params_from_intake_answers(ai))

    return _answers_from_params(
        params,
        fallback_objective=achievement or goal_title or "7-day goal",
    )


def _params_from_intake_answers(ai: dict[str, Any]) -> dict[str, Any]:
    """Extract structured fields from AI-verified parsed answers (fallback: re-parse raw text)."""
    out: dict[str, Any] = {}
    parsed = ai.get("parsed_answers") or {}
    answers = ai.get("answers") or {}
    questions: list[dict[str, Any]] = ai.get("questions") or []

    if questions:
        head_id = str(questions[0].get("id") or "")
        head_val = parsed.get(head_id)
        if isinstance(head_val, str) and head_val.strip():
            out["objective"] = head_val.strip()[:500]

    if "tasks_per_day" in parsed:
        try:
            out["tasks_per_day"] = max(1, min(5, int(parsed["tasks_per_day"])))
        except (TypeError, ValueError):
            pass
    elif answers.get("tasks_per_day"):
        from todai.goal_planner.interrogation import parse_answer

        r = parse_answer("tasks_per_day", str(answers["tasks_per_day"]))
        if r.valid:
            out["tasks_per_day"] = r.parsed

    if "skip_days" in parsed and isinstance(parsed["skip_days"], list):
        out["skip_days"] = [
            int(d) for d in parsed["skip_days"] if isinstance(d, (int, float)) and 0 <= int(d) <= 6
        ]
    elif answers.get("skip_days") is not None and str(answers.get("skip_days")).strip():
        r = parse_skip_days(str(answers["skip_days"]))
        if r.valid:
            out["skip_days"] = r.parsed

    for qid, val in parsed.items():
        if qid in ("outcome", "objective", "goal") and isinstance(val, str) and val.strip():
            out["objective"] = val.strip()[:500]
            break

    return out

def _build_intake_questions(clarifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One AI clarification + fixed tasks/day + skip days."""
    head: dict[str, Any] | None = None
    for q in clarifications or []:
        if isinstance(q, dict) and q.get("text"):
            head = {"id": str(q.get("id") or "outcome"), "text": str(q["text"]).strip()}
            break
        if isinstance(q, str) and q.strip():
            head = {"id": "outcome", "text": q.strip()}
            break
    if not head:
        head = dict(_FALLBACK_CLARIFICATION)
    text = head["text"]
    if "question 1 of 3" not in text.lower():
        head = {**head, "text": f"**Question 1 of 3 — Your goal**\n{text.lstrip()}"}
    return [head, *_FIXED_TAIL_QUESTIONS]


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
    skip = params.get("skip_days")
    if skip is None:
        skip_list: list[int] = []
        skip_display = format_skip_days([])
    elif isinstance(skip, list):
        skip_list = sorted({int(d) for d in skip if isinstance(d, (int, float)) and 0 <= int(d) <= 6})
        skip_display = format_skip_days(skip_list)
    else:
        r = parse_skip_days(str(skip))
        skip_list = list(r.parsed) if r.valid and isinstance(r.parsed, list) else []
        skip_display = r.display or format_skip_days(skip_list)

    out: dict[str, Any] = {}
    for step, val, display in (
        ("objective", obj, obj[:80]),
        ("tasks_per_day", tpd, str(tpd)),
        ("skip_days", skip_list, skip_display),
        ("difficulty", diff, diff),
    ):
        out[step] = {"valid": True, "parsed": val, "raw": str(val), "display": display}
    return out


def _parse_init(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    clarification = raw.get("clarification_question")
    questions = raw.get("questions")
    cleaned: list[dict[str, str]] = []
    if isinstance(clarification, dict) and clarification.get("text"):
        cleaned.append(
            {
                "id": str(clarification.get("id") or "outcome"),
                "text": str(clarification["text"]).strip(),
            }
        )
    elif isinstance(questions, list):
        for i, q in enumerate(questions[:1]):
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
        "clarification_question": cleaned[0],
        "defaults": defaults,
    }


def uses_ai_intake(session: dict[str, Any], ui_mode: str) -> bool:
    return ui_mode == "new_goal" and session.get("intake_style") == "ai"
