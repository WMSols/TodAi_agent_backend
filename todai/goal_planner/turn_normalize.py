"""Groq-first normalization for goal planner decisions; static code verifies before writes."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.intake_validate import verify_intake_field
from todai.goal_planner.interrogation import (
    _answer_label,
    is_goal_cancel_message,
    parse_confirmation,
    plan_difficulty,
    plan_minutes_per_day,
    plan_skip_days,
    try_apply_confirm_edits,
)

logger = logging.getLogger(__name__)

ConfirmationChoice = Literal["yes", "no", "unclear"]

__CONFIRM_SYSTEM = (
    'Yes/no classifier. Return JSON only: {"confirmation":"yes"|"no"|"unclear"}\n'
    'yes = user intends to proceed, approve, or agree in any form\n'
    'no = user intends to decline, cancel, or reject in any form\n'
    'unclear = intent is not clearly yes or no, or includes questions or mixed instructions\n'
    'If user mixes agreement with extra instructions, treat as "yes"'
)
_CONFIRM_SYSTEM = __CONFIRM_SYSTEM


@dataclass(frozen=True)
class ConfirmationResult:
    choice: ConfirmationChoice
    source: str = "fallback"


def normalize_confirmation(
    message: str,
    *,
    context: dict[str, Any] | None = None,
    allow_groq: bool = True,
) -> ConfirmationResult:
    """Understand yes/no from natural language; static parse_confirmation is fallback only."""
    text = (message or "").strip()
    if not text:
        return ConfirmationResult("unclear", source="empty")

    if allow_groq and GROQ_API_KEY:
        groq_choice = _groq_confirmation(text, context or {})
        if groq_choice is not None:
            return ConfirmationResult(groq_choice, source="groq")

    return ConfirmationResult(parse_confirmation(text), source="fallback")


def _groq_confirmation(text: str, context: dict[str, Any]) -> ConfirmationChoice | None:
    payload = {
        "user_reply": text[:500],
        "context": {k: v for k, v in context.items() if v is not None},
    }
    try:
        raw = groq_chat_json(
            [
                {"role": "system", "content": _CONFIRM_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            phase="goal_confirm_normalize",
            max_tokens=40,
            temperature=0,
        )
    except Exception as e:
        logger.warning("goal confirm normalize Groq failed: %s", e)
        return None

    if not isinstance(raw, dict):
        return None
    choice = str(raw.get("confirmation") or "").lower().strip()
    if choice in ("yes", "no", "unclear"):
        return choice
    return None


_CONFIRM_EDIT_FIELDS = frozenset(
    {"objective", "tasks_per_day", "skip_days", "difficulty", "minutes_per_day"}
)

_CONFIRM_EDITS_SYSTEM = (
    "Pre-build plan review. JSON: "
    '{"status":"ok"|"none","changes":object,"replyText":string}\n'
    "Read current_settings in the user payload.understand the user requirement and data output requiremtn changes: ONLY fields the user changes.\n"
    "skip_days int[] 0=Mon..6=Sun NOT ISO. [] = no skip days.\n"
    "MERGE skip_days when user says add/also/as well/in addition/include "
    "(e.g. current [5,6] + 'add Friday' → [4,5,6]). "
    "REPLACE only when user says only/instead/set skip to/change skip to.\n"
    "weekends→[5,6]; weekdays→[0,1,2,3,4]. objective; tasks_per_day 1-5; difficulty;.\n"
    "status none: yes/no alone, thanks, delete/discard/cancel, unrelated chat.\n"
    "This is NOT create/build — only update settings. replyText ≤15 words."
)


@dataclass
class ConfirmEditResult:
    answers: dict[str, Any]
    ack: str | None = None
    source: str = "none"
    changed_fields: list[str] = field(default_factory=list)


def apply_confirm_edits(
    message: str,
    answers: dict[str, Any],
    *,
    goal_title: str = "",
    default_objective: str = "",
    allow_groq: bool = True,
) -> ConfirmEditResult:
    """
    Groq-first: parse one message that may update several plan settings.
    Static parsers verify each value; try_apply_confirm_edits is offline fallback.
    """
    text = (message or "").strip()
    if not text:
        return ConfirmEditResult(dict(answers))

    if is_goal_cancel_message(text):
        return ConfirmEditResult(dict(answers))

    working = copy.deepcopy(answers)

    if allow_groq and GROQ_API_KEY:
        groq_result = _groq_confirm_edits(
            text,
            working,
            goal_title=goal_title,
            default_objective=default_objective,
        )
        if groq_result.changed_fields:
            return groq_result

    updated, ack = try_apply_confirm_edits(
        text, working, default_objective=default_objective
    )
    if ack:
        fields = _diff_answer_fields(answers, updated)
        return ConfirmEditResult(updated, ack, "static", fields)
    return ConfirmEditResult(dict(answers))


def _diff_answer_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key in _CONFIRM_EDIT_FIELDS:
        if (before.get(key) or {}).get("parsed") != (after.get(key) or {}).get("parsed"):
            if key in after:
                changed.append(key)
    return changed


def _settings_snapshot(answers: dict[str, Any], *, goal_title: str) -> dict[str, Any]:
    skip = plan_skip_days(answers)
    return {
        "goal_title": (goal_title or "").strip(),
        "objective": _answer_label(answers, "objective"),
        "tasks_per_day": _answer_label(answers, "tasks_per_day"),
        "skip_days": skip,
        "difficulty": plan_difficulty(answers),
        "minutes_per_day": plan_minutes_per_day(answers),
    }


def _groq_confirm_edits(
    message: str,
    answers: dict[str, Any],
    *,
    goal_title: str,
    default_objective: str,
) -> ConfirmEditResult:
    payload = {
        "user_message": message[:600],
        "current_settings": _settings_snapshot(answers, goal_title=goal_title),
    }
    try:
        raw = groq_chat_json(
            [
                {"role": "system", "content": _CONFIRM_EDITS_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            phase="goal_confirm_edits",
            max_tokens=200,
            temperature=0,
        )
    except Exception as e:
        logger.warning("goal confirm edits Groq failed: %s", e)
        return ConfirmEditResult(dict(answers))

    if not isinstance(raw, dict):
        return ConfirmEditResult(dict(answers))

    status = str(raw.get("status") or "").lower().strip()
    if status == "none":
        return ConfirmEditResult(dict(answers))

    changes = raw.get("changes")
    if not isinstance(changes, dict) or not changes:
        return ConfirmEditResult(dict(answers))

    working = copy.deepcopy(answers)
    acks: list[str] = []
    changed_fields: list[str] = []

    for field_name, raw_val in changes.items():
        if field_name not in _CONFIRM_EDIT_FIELDS or raw_val is None:
            continue
        prior_skip = plan_skip_days(answers) if field_name == "skip_days" else None
        verified = verify_intake_field(
            field_name,
            raw_val,
            raw_text=message,
            default_objective=default_objective,
            prior_skip_days=prior_skip,
            confirm_edit=True,
        )
        if not verified.valid:
            continue
        working[field_name] = {
            "valid": True,
            "parsed": verified.parsed,
            "raw": message,
            "display": verified.display or str(verified.parsed),
        }
        changed_fields.append(field_name)
        label = field_name.replace("_", " ")
        disp = verified.display or str(verified.parsed)
        acks.append(f"{label} → {disp}")

    if not changed_fields:
        return ConfirmEditResult(dict(answers))

    reply = str(raw.get("replyText") or raw.get("reply_text") or "").strip()
    if acks:
        ack = "; ".join(acks)
    elif reply and len(reply) <= 200:
        ack = reply
    else:
        ack = reply or None
    return ConfirmEditResult(working, ack, "groq", changed_fields)
