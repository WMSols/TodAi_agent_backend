"""Per-answer validation for AI goal intake (Groq normalize first + static verify; local fallback)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.interrogation import (
    ParseResult,
    format_skip_days,
    parse_answer,
    parse_skip_days,
)

logger = logging.getLogger(__name__)

IntakeStatus = Literal["ok", "aborted"]

_SKIP_DAYS_GROQ_RULES = (
    "skip_days: Python ints 0=Mon..6=Sun (NOT ISO). 'monday'→[0]; 'weekends'→[5,6]. "
    "[] = no skip days: none, no skip, no days to skip, no any days, not any day to skip. "
    "One weekday is a complete answer. status ok whenever parsedValue is valid — never abort valid []."
)

_GROQ_STRUCTURED_SYSTEM = (
    "Normalize ONE intake answer. JSON: "
    '{"status":"ok"|"aborted","replyText":string,"parsedValue":value}\n'
    "ok if on-topic AND parsedValue matches fieldType. Abort ONLY if empty or totally unrelated.\n"
    "types: tasks_per_day int 1-5; skip_days int[]; "
    f"skip_days: {_SKIP_DAYS_GROQ_RULES} "
    "objective/open string; difficulty easy|medium|hard; minutes int 5-480.\n"
    "replyText ≤12 words."
)

_CHITCHAT = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|idk|dunno|whatever|maybe)\.?$",
    re.I,
)

_YES_NO_QUESTION = re.compile(
    r"\b(?:any|do you|are there|existing|prior|already|have you|"
    r"experience|skills?|knowledge|background|familiar)\b",
    re.I,
)

_YES_NO_ANSWER = re.compile(
    r"\b(?:no|none|nothing|nope|not really|don't|do not|doesn't|"
    r"haven't|have not|never|zero|beginner|starting from scratch|"
    r"yes|yeah|yep|some|a little|basic)\b",
    re.I,
)

_WEEKDAY_NAME_TO_DOW: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


@dataclass(frozen=True)
class IntakeValidation:
    status: IntakeStatus
    reply_text: str
    source: str = "local"
    parsed_value: Any = None


# Fixed intake step ids — must win over fuzzy question-text regexes.
_STRUCTURED_KINDS = frozenset(
    {
        "tasks_per_day",
        "skip_days",
        "minutes_per_day",
        "time_commitment",
        "difficulty",
        "objective",
        "schedule",
        "open",
    }
)

_KNOWN_QID_KINDS: dict[str, str] = {
    "skip_days": "skip_days",
    "tasks_per_day": "tasks_per_day",
    "tasks": "tasks_per_day",
    "minutes_per_day": "minutes_per_day",
    "minutes": "minutes_per_day",
    "difficulty": "difficulty",
    "intensity": "difficulty",
    "outcome": "objective",
    "objective": "objective",
    "goal": "objective",
}


def infer_question_kind(question_id: str, question_text: str) -> str:
    """Map AI question id/text to a validation profile."""
    qid = (question_id or "").lower()
    known = _KNOWN_QID_KINDS.get(qid)
    if known:
        return known

    blob = f"{qid} {(question_text or '').lower()}"
    if re.search(r"\bskip\b.*\b(day|week)", blob) or re.search(r"\bwhich weekdays\b", blob):
        return "skip_days"
    if "tasks_per" in qid or re.search(
        r"\b(?:how many|number of)\b.*\btasks?\b.*\b(?:per|each|on)\b.*\b(?:day|daily)\b",
        blob,
    ) or re.search(r"\btasks?\s+per\s+(?:active\s+)?day\b", blob):
        return "tasks_per_day"
    if re.search(r"\b(hours?|minutes?|mins?|time)\b", blob) and re.search(
        r"\b(day|daily|per|dedicat|commit|spend)\b", blob
    ):
        return "time_commitment"
    if re.search(r"\b(minute|hour|time)\b.*\b(day|daily|per)\b", blob):
        return "minutes_per_day"
    if re.search(r"\b(intensity|intense|difficult|challenge)\b", blob):
        return "difficulty"
    if "schedule" in qid or re.search(r"\bdays?\s+per\s+week\b", blob):
        return "schedule"
    if re.search(r"\b(outcome|achieve|objective|measurable|7[- ]?day)\b", blob):
        return "objective"
    return "open"


def validate_intake_answer(
    *,
    question: dict[str, Any],
    answer: str,
    goal_title: str = "",
    default_objective: str = "",
    allow_groq: bool = True,
) -> IntakeValidation:
    """Validate one intake answer; save parsed_value only when status is ok."""
    text = (answer or "").strip()
    qtext = str(question.get("text") or "").strip()
    qid = str(question.get("id") or "")

    if not text:
        return IntakeValidation(
            status="aborted",
            reply_text="Please send a short answer — I can't use an empty message.",
            source="local",
        )

    if _CHITCHAT.match(text) and len(text) < 12:
        return IntakeValidation(
            status="aborted",
            reply_text="That doesn't answer the question yet — please try again.",
            source="local",
        )

    kind = _KNOWN_QID_KINDS.get(qid.lower()) or infer_question_kind(qid, qtext)

    if allow_groq and kind in _STRUCTURED_KINDS:
        groq = _validate_groq_structured(
            kind,
            qtext,
            text,
            goal_title,
            allow_groq=allow_groq,
        )
        if groq is not None:
            if groq.status == "aborted":
                if kind == "skip_days":
                    local_fb = _local_structured_fallback(
                        kind, text, default_objective=default_objective
                    )
                    if local_fb is not None:
                        return local_fb
                    empty_skip = _normalize_skip_parsed(groq.parsed_value)
                    if empty_skip is not None and empty_skip == []:
                        return IntakeValidation(
                            status="ok",
                            reply_text="No skip days — tasks every day.",
                            source="groq_reconcile",
                            parsed_value=[],
                        )
                if kind in ("open", "objective") and _lenient_accept(qtext, text):
                    fallback = _validate_local("objective", text, default_objective=default_objective)
                    if fallback.valid:
                        return _validation_from_parse(fallback, kind, source="local_override")
                    return IntakeValidation(
                        status="ok",
                        reply_text="Got it.",
                        source="local_override",
                        parsed_value=text[:500],
                    )
                local_fb = _local_structured_fallback(
                    kind, text, default_objective=default_objective
                )
                if local_fb is not None:
                    return local_fb
                return groq

            verified = _verify_parsed_value(
                kind,
                groq.parsed_value,
                raw_text=text,
                default_objective=default_objective,
            )
            if verified.valid:
                ack = groq.reply_text or _ack_from_parse(verified, kind)
                return IntakeValidation(
                    status="ok",
                    reply_text=ack,
                    source="groq_verified",
                    parsed_value=verified.parsed,
                )
            hint = verified.hint or groq.reply_text or "Please answer in the format the question asks for."
            local_fb = _local_structured_fallback(
                kind, text, default_objective=default_objective
            )
            if local_fb is not None:
                return local_fb
            return IntakeValidation(status="aborted", reply_text=hint, source="groq_rejected")

    # Offline / Groq unavailable: static parsers verify only (no Groq normalization).
    if _is_yes_no_question(qtext) and _is_yes_no_answer(text):
        return IntakeValidation(
            status="ok",
            reply_text="Got it — noted.",
            source="local",
            parsed_value=text,
        )

    local = _validate_local(kind, text, default_objective=default_objective)
    if local.valid:
        return _validation_from_parse(local, kind, source="local")

    if kind in _STRUCTURED_KINDS:
        return IntakeValidation(
            status="aborted",
            reply_text=local.hint or "Please answer in the format the question asks for.",
            source="local",
        )

    if _lenient_accept(qtext, text):
        return IntakeValidation(
            status="ok",
            reply_text="Got it.",
            source="local",
            parsed_value=text[:500],
        )

    return IntakeValidation(
        status="aborted",
        reply_text="Please add a bit more detail so I can plan your week.",
        source="local",
    )


def _validation_from_parse(result: ParseResult, kind: str, *, source: str) -> IntakeValidation:
    ack = result.display or str(result.parsed)
    if kind == "skip_days":
        reply = f"Got it — {ack}."
    elif kind in ("tasks_per_day", "minutes_per_day", "difficulty", "time_commitment"):
        reply = f"Got it — **{ack}**."
    elif kind == "objective":
        reply = "Got it — saved your objective."
    else:
        reply = "Got it."
    return IntakeValidation(
        status="ok",
        reply_text=reply,
        source=source,
        parsed_value=result.parsed,
    )


def _ack_from_parse(result: ParseResult, kind: str) -> str:
    return _validation_from_parse(result, kind, source="local").reply_text


def _validate_local(kind: str, text: str, *, default_objective: str) -> ParseResult:
    if kind == "tasks_per_day":
        return parse_answer("tasks_per_day", text)
    if kind == "skip_days":
        return parse_skip_days(text)
    if kind in ("minutes_per_day", "time_commitment"):
        return _parse_time_commitment(text)
    if kind == "difficulty":
        return parse_answer("difficulty", text)
    if kind == "objective":
        return parse_answer("objective", text, default_objective=default_objective)
    if kind == "schedule":
        return _validate_schedule_local(text)
    return ParseResult(valid=True, parsed=text[:500])


def verify_intake_field(
    kind: str,
    parsed: Any,
    *,
    raw_text: str = "",
    default_objective: str = "",
    prior_skip_days: list[int] | None = None,
    confirm_edit: bool = False,
) -> ParseResult:
    """Public static verifier for Groq-normalized intake or confirm-edit values."""
    return _verify_parsed_value(
        kind,
        parsed,
        raw_text=raw_text,
        default_objective=default_objective,
        prior_skip_days=prior_skip_days,
        confirm_edit=confirm_edit,
    )


def _text_mentions_tasks_per_day(text: str) -> bool:
    lower = (text or "").lower()
    if re.search(r"\b([1-5])\b", lower):
        return True
    if re.search(r"\b(?:one|two|three|four|five)\b", lower) and re.search(
        r"\b(?:task|tasks|per day|daily)\b", lower
    ):
        return True
    if re.search(r"\b\d+\s*tasks?\b", lower):
        return True
    return False


def _text_mentions_skip_change(text: str) -> bool:
    lower = (text or "").lower()
    if _text_mentions_weekday(lower):
        return True
    return bool(
        re.search(
            r"\b(?:none|no skip|no days? to skip|no any days?|not any day|every day|all days|"
            r"weekends?|weekdays?|don't skip|do not skip)\b",
            lower,
        )
    )


def _verify_parsed_value(
    kind: str,
    parsed: Any,
    *,
    raw_text: str,
    default_objective: str = "",
    prior_skip_days: list[int] | None = None,
    confirm_edit: bool = False,
) -> ParseResult:
    """Static verify after Groq: user text wins when parseable; else require text evidence."""
    if kind == "tasks_per_day":
        local = parse_answer("tasks_per_day", raw_text)
        if local.valid:
            return local
        if isinstance(parsed, (int, float)):
            n = int(parsed)
            if 1 <= n <= 5 and _text_mentions_tasks_per_day(raw_text):
                return ParseResult(valid=True, parsed=n, display=str(n))
        if isinstance(parsed, str):
            sub = parse_answer("tasks_per_day", parsed)
            if sub.valid:
                return sub
        return ParseResult(
            valid=False,
            hint="Send a whole number from **1 to 5** (example: 2 or *two tasks*).",
        )

    if kind == "skip_days":
        return _verify_skip_days(
            parsed,
            raw_text,
            prior_skip_days=prior_skip_days,
            confirm_edit=confirm_edit,
        )

    if kind == "difficulty":
        if isinstance(parsed, str):
            val = parsed.lower().strip()
            if val in ("easy", "medium", "hard"):
                return ParseResult(valid=True, parsed=val, display=val)
        return parse_answer("difficulty", raw_text)

    if kind in ("minutes_per_day", "time_commitment"):
        if isinstance(parsed, (int, float)):
            mins = int(parsed)
            if 5 <= mins <= 480:
                return ParseResult(
                    valid=True,
                    parsed=mins,
                    display=f"{mins} minutes per day",
                )
        return _parse_time_commitment(raw_text)

    if kind == "objective":
        from todai.goal_planner.interrogation import extract_objective_edit_text, is_goal_cancel_message

        if is_goal_cancel_message(raw_text):
            return ParseResult(valid=False, hint="Say **yes** to build, **no** to restart, or describe setting changes.")
        edit_text = extract_objective_edit_text(raw_text)
        local_obj = parse_answer("objective", edit_text, default_objective=default_objective)
        if local_obj.valid and edit_text.strip() != raw_text.strip():
            return local_obj
        if isinstance(parsed, str) and len(parsed.strip()) >= 3:
            val = parsed.strip()[:500]
            verified = parse_answer("objective", val, default_objective=default_objective)
            if verified.valid:
                return ParseResult(valid=True, parsed=verified.parsed, display=str(verified.parsed)[:80])
        return parse_answer("objective", edit_text, default_objective=default_objective)

    if kind == "schedule":
        if isinstance(parsed, str) and len(parsed.strip()) >= 5:
            return ParseResult(valid=True, parsed=parsed.strip()[:500])
        return _validate_schedule_local(raw_text)

    if isinstance(parsed, str) and len(parsed.strip()) >= 2:
        return ParseResult(valid=True, parsed=parsed.strip()[:500])
    if len(raw_text.strip()) >= 2:
        return ParseResult(valid=True, parsed=raw_text.strip()[:500])
    return ParseResult(valid=False, hint="Please add a bit more detail.")


def _text_mentions_weekday(text: str) -> bool:
    lower = (text or "").lower()
    if re.search(r"\bweekends?\b", lower):
        return True
    if re.search(r"\bweekdays?\b", lower) and "weekend" not in lower:
        return True
    return any(
        re.search(rf"\b{re.escape(name)}\b", lower) for name in _WEEKDAY_NAME_TO_DOW
    )


def _verify_skip_days(
    parsed: Any,
    raw_text: str,
    *,
    prior_skip_days: list[int] | None = None,
    confirm_edit: bool = False,
) -> ParseResult:
    """Intake: static parse wins. Confirm edits: trust Groq merged skip_days ints."""
    normalized = _normalize_skip_parsed(parsed)

    if confirm_edit and normalized is not None and _text_mentions_skip_change(raw_text):
        return ParseResult(
            valid=True,
            parsed=normalized,
            display=format_skip_days(normalized),
        )

    local = parse_skip_days(raw_text)
    if local.valid:
        return local

    if not _text_mentions_skip_change(raw_text):
        return local

    if normalized is not None:
        return ParseResult(
            valid=True,
            parsed=normalized,
            display=format_skip_days(normalized),
        )
    return local


def _local_structured_fallback(
    kind: str,
    text: str,
    *,
    default_objective: str = "",
) -> IntakeValidation | None:
    if kind not in _STRUCTURED_KINDS:
        return None
    local = _validate_local(kind, text, default_objective=default_objective)
    if local.valid:
        return _validation_from_parse(local, kind, source="local_fallback")
    return None


def _normalize_skip_parsed(parsed: Any) -> list[int] | None:
    if parsed is None:
        return []
    if isinstance(parsed, list):
        out: set[int] = set()
        for item in parsed:
            if isinstance(item, (int, float)) and 0 <= int(item) <= 6:
                out.add(int(item))
            elif isinstance(item, str):
                key = item.lower().strip()
                if key in _WEEKDAY_NAME_TO_DOW:
                    out.add(_WEEKDAY_NAME_TO_DOW[key])
                elif key.isdigit() and 0 <= int(key) <= 6:
                    out.add(int(key))
        return sorted(out)
    if isinstance(parsed, str):
        lower = parsed.lower().strip()
        if lower in ("none", "[]", ""):
            return []
        r = parse_skip_days(parsed)
        if r.valid and isinstance(r.parsed, list):
            return list(r.parsed)
    return None


def _is_yes_no_question(question_text: str) -> bool:
    return bool(_YES_NO_QUESTION.search(question_text or ""))


def _is_yes_no_answer(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 1:
        return False
    if _YES_NO_ANSWER.search(t):
        return True
    if len(t) <= 40 and re.search(r"\bno\b", t, re.I):
        return True
    return False


def _parse_time_commitment(text: str) -> ParseResult:
    parsed = parse_answer("minutes_per_day", text)
    if parsed.valid:
        return parsed
    lower = (text or "").lower()
    hm = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r)?\b", lower)
    if hm:
        hours = float(hm.group(1))
        if 0 < hours <= 12:
            mins = int(hours * 60)
            label = f"{hours:g} hour(s) per day" if hours != 1 else "1 hour per day"
            return ParseResult(valid=True, parsed=mins, display=label)
    return ParseResult(
        valid=False,
        hint="Give a daily time budget (e.g. **30 minutes**, **1 hour**, or **45 mins per day**).",
    )


def _lenient_accept(question_text: str, answer: str) -> bool:
    """Accept brief but sensible answers without calling Groq (or override strict Groq)."""
    text = (answer or "").strip()
    if len(text) < 2:
        return False
    if _is_yes_no_question(question_text) and _is_yes_no_answer(text):
        return True
    if _parse_time_commitment(text).valid:
        return True
    if _CHITCHAT.match(text):
        return False
    if len(text) >= 3 and re.search(r"[a-zA-Z]{3,}", text):
        return True
    return False


def _validate_schedule_local(text: str) -> ParseResult:
    lower = text.lower()
    has_time = bool(
        re.search(r"\b\d+\s*(?:min|mins|minutes|hour|hours|h)\b", lower)
        or re.search(r"\b\d{1,3}\b", lower)
    )
    has_days = bool(
        re.search(r"\b\d+\s*days?\b", lower)
        or re.search(r"\b(every day|daily|weekday|weekend|mon|tue|wed|thu|fri|sat|sun)\b", lower)
        or re.search(r"\b\d+\s*(?:to|-)\s*\d+\b", lower)
    )
    if has_time or has_days:
        return ParseResult(valid=True, parsed=text)
    if len(text) >= 12:
        return ParseResult(valid=True, parsed=text)
    return ParseResult(
        valid=False,
        hint="Include how many days per week and/or minutes per day (e.g. 5 days, 30 minutes).",
    )


def _validate_groq_structured(
    kind: str,
    question: str,
    answer: str,
    goal_title: str,
    *,
    allow_groq: bool,
) -> IntakeValidation | None:
    if not allow_groq or not GROQ_API_KEY:
        return None
    field_type = kind if kind != "open" else "open"
    payload = {
        "goal": (goal_title or "goal")[:120],
        "fieldType": field_type,
        "question": question[:400],
        "answer": answer[:500],
    }
    try:
        raw = groq_chat_json(
            [
                {"role": "system", "content": _GROQ_STRUCTURED_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            phase="goal_intake_validate",
            max_tokens=120,
            temperature=0,
        )
    except Exception as e:
        logger.warning("goal intake validate Groq failed: %s", e)
        return None

    return _parse_groq_structured(raw)


def _parse_groq_structured(raw: dict[str, Any]) -> IntakeValidation | None:
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "").lower().strip()
    reply = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
    parsed = raw.get("parsedValue")
    if parsed is None and "parsed_value" in raw:
        parsed = raw.get("parsed_value")
    if status not in ("ok", "aborted"):
        return None
    if not reply:
        reply = "Got it." if status == "ok" else "Please answer the question above."
    reply = reply[:240]
    return IntakeValidation(
        status=status,
        reply_text=reply,
        source="groq",
        parsed_value=parsed,
    )
