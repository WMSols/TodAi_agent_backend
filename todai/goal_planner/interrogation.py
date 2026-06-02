"""Static onboarding questions + validation for goal week plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

StepId = Literal["objective", "difficulty", "tasks_per_day", "minutes_per_day"]

STEPS: tuple[StepId, ...] = (
    "objective",
    "difficulty",
    "tasks_per_day",
    "minutes_per_day",
)

QUESTIONS: dict[StepId, str] = {
    "objective": (
        "**Question 1 of 4 — Objective**\n"
        "What do you want to achieve in the next 7 days? "
        "Write one clear goal (e.g. “Lose 2 kg” or “Practice Python 30 minutes daily”)."
    ),
    "difficulty": (
        "**Question 2 of 4 — Difficulty**\n"
        "How hard should this feel? Reply with exactly one word: **easy**, **medium**, or **hard**."
    ),
    "tasks_per_day": (
        "**Question 3 of 4 — Tasks per day**\n"
        "How many separate tasks per day? Send one number from **1** to **5** (example: `2`)."
    ),
    "minutes_per_day": (
        "**Question 4 of 4 — Time per day**\n"
        "How many minutes per day can you spend on this goal?\n"
        "Examples: `45`, `1 hour`, or a range like `10 to 15 minutes`."
    ),
}

_DIFFICULTY_MAP = {
    "easy": "easy",
    "e": "easy",
    "light": "easy",
    "beginner": "easy",
    "medium": "medium",
    "med": "medium",
    "moderate": "medium",
    "normal": "medium",
    "hard": "hard",
    "h": "hard",
    "tough": "hard",
    "difficult": "hard",
    "challenging": "hard",
}

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*h(?:ours?|r)?)|(?:(\d+)\s*m(?:in(?:ute)?s?)?)|^(\d+)\s*$",
    re.I,
)


@dataclass(frozen=True)
class ParseResult:
    valid: bool
    parsed: Any = None
    hint: str = ""
    display: str = ""  # human-readable value shown back to user


def answers_complete(answers: dict[str, Any]) -> bool:
    for step in STEPS:
        slot = answers.get(step)
        if not isinstance(slot, dict) or not slot.get("valid"):
            return False
    return True


def next_missing_step(answers: dict[str, Any]) -> StepId | None:
    for step in STEPS:
        slot = answers.get(step)
        if not isinstance(slot, dict) or not slot.get("valid"):
            return step
    return None


def current_step(session: dict[str, Any]) -> StepId | None:
    return session.get("intake_step") or next_missing_step(session.get("answers") or {})


def parse_answer(step: StepId, text: str, *, default_objective: str = "") -> ParseResult:
    raw = (text or "").strip()
    if not raw:
        return ParseResult(valid=False, hint="Please send a short reply — I couldn't use an empty message.")

    if step == "objective":
        return _parse_objective(raw, default_objective=default_objective)
    if step == "difficulty":
        return _parse_difficulty(raw)
    if step == "tasks_per_day":
        return _parse_tasks_per_day(raw)
    if step == "minutes_per_day":
        return _parse_minutes_per_day(raw)
    return ParseResult(valid=False, hint="Unknown question step.")


def _parse_objective(raw: str, *, default_objective: str) -> ParseResult:
    if raw.lower() in ("ok", "yes", "y", "same", "that's fine") and default_objective:
        val = default_objective.strip()
    else:
        val = raw.strip()
    if len(val) < 3:
        return ParseResult(
            valid=False,
            hint="Please describe your objective in a few words (e.g. “weight loss” or “learn Python”).",
        )
    if len(val) > 500:
        val = val[:500]
    return ParseResult(valid=True, parsed=val)


def _parse_difficulty(raw: str) -> ParseResult:
    words = re.findall(r"[a-z]+", raw.lower())
    for w in words:
        if w in _DIFFICULTY_MAP:
            return ParseResult(valid=True, parsed=_DIFFICULTY_MAP[w])
    joined = " ".join(words)
    for phrase, val in (
        ("very hard", "hard"),
        ("pretty hard", "hard"),
        ("kind of hard", "hard"),
        ("very easy", "easy"),
    ):
        if phrase in joined:
            return ParseResult(valid=True, parsed=val)
    return ParseResult(
        valid=False,
        hint="Please reply with **easy**, **medium**, or **hard** only.",
    )


def _parse_tasks_per_day(raw: str) -> ParseResult:
    m = re.search(r"\b([1-5])\b", raw)
    if m:
        return ParseResult(valid=True, parsed=int(m.group(1)))
    m2 = re.search(r"\b(\d+)\b", raw)
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 5:
            return ParseResult(valid=True, parsed=n)
        return ParseResult(valid=False, hint="Choose between **1 and 5** tasks per day.")
    return ParseResult(
        valid=False,
        hint="Send a whole number from **1 to 5** (example: 2).",
    )


def _parse_minutes_per_day(raw: str) -> ParseResult:
    normalized = raw.lower().replace("mints", "mins").replace("mint", "min")
    range_pair = _minutes_range_from_text(normalized)
    if range_pair is not None:
        lo, hi, avg = range_pair
        if hi > 480:
            return ParseResult(
                valid=False,
                hint="Daily time seems too high. Use at most **480 minutes** (8 hours) per day.",
            )
        if lo < 5:
            return ParseResult(valid=False, hint="Please use at least **5 minutes** per day in your range.")
        display = f"{lo}–{hi} minutes per day (~{avg} min average)"
        return ParseResult(valid=True, parsed=avg, display=display)

    minutes = _minutes_from_text(normalized)
    if minutes is None:
        return ParseResult(
            valid=False,
            hint=(
                "I need a daily time budget. Examples: **30**, **45 minutes**, **1 hour**, "
                "or **10 to 15 minutes**."
            ),
        )
    if minutes < 5:
        return ParseResult(valid=False, hint="Please allow at least **5 minutes** per day.")
    if minutes > 480:
        return ParseResult(valid=False, hint="Please keep it at **480 minutes** (8 hours) or less per day.")
    return ParseResult(valid=True, parsed=minutes, display=f"{minutes} minutes per day")


def _minutes_range_from_text(lower: str) -> tuple[int, int, int] | None:
    m = re.search(
        r"(\d+)\s*(?:to|-|–)\s*(\d+)\s*(?:min|mins|minutes|minute)?",
        lower,
    )
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo > hi:
        lo, hi = hi, lo
    if hi - lo > 180:
        return None
    avg = (lo + hi) // 2
    return lo, hi, avg


def _minutes_from_text(raw: str) -> int | None:
    lower = raw.lower().strip()
    if _minutes_range_from_text(lower):
        return None
    if re.fullmatch(r"\d+", lower):
        return int(lower)
    total = 0
    found = False
    for h, m, solo in _DURATION_RE.findall(lower):
        if h:
            total += int(h) * 60
            found = True
        if m:
            total += int(m)
            found = True
        if solo:
            total += int(solo)
            found = True
    if found:
        return total
    m = re.search(r"\b(\d{1,3})\b", lower)
    if m:
        return int(m.group(1))
    return None


def _answer_label(answers: dict[str, Any], step: str) -> str:
    slot = answers.get(step) or {}
    if slot.get("display"):
        return str(slot["display"])
    parsed = slot.get("parsed", "")
    if step == "minutes_per_day" and parsed != "":
        return f"{parsed} minutes per day"
    return str(parsed)


def confirmation_prompt(answers: dict[str, Any]) -> str:
    return (
        "**Review your 7-day plan settings**\n"
        f"1. Objective: {_answer_label(answers, 'objective')}\n"
        f"2. Difficulty: {_answer_label(answers, 'difficulty')}\n"
        f"3. Tasks per day: {_answer_label(answers, 'tasks_per_day')}\n"
        f"4. Time per day: {_answer_label(answers, 'minutes_per_day')}\n\n"
        "Reply **yes** to create tasks in your free calendar slots.\n"
        "To change something, say e.g. “change difficulty to hard”."
    )


_ACTIVE_ACK = re.compile(
    r"^(ok|okay|k|thanks|thank you|cool|great|got it|nice|sounds good)\.?$",
    re.I,
)


def is_active_acknowledgment(text: str) -> bool:
    return bool(_ACTIVE_ACK.match((text or "").strip()))


def parse_confirmation(text: str) -> Literal["yes", "no", "unclear"]:
    t = (text or "").strip().lower()
    if t in ("yes", "y", "ok", "okay", "sure", "go", "create", "generate", "do it", "confirm"):
        return "yes"
    if t in ("no", "n", "wait", "stop", "cancel"):
        return "no"
    if re.search(r"\b(yes|yeah|yep|go ahead|looks good)\b", t):
        return "yes"
    return "unclear"
