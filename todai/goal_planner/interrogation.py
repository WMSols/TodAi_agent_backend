"""Static onboarding questions + validation for goal week plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

StepId = Literal["objective", "tasks_per_day", "skip_days", "difficulty", "minutes_per_day"]

STEPS: tuple[StepId, ...] = (
    "objective",
    "tasks_per_day",
    "skip_days",
)

DEFAULT_PLAN_MINUTES = 30

QUESTIONS: dict[StepId, str] = {
    "objective": (
        "**Question 1 of 3 — Your goal**\n"
        "What do you want to achieve in the next 7 days? "
        "Write one clear outcome (e.g. “Lose 3 kg” or “Learn basic algebra”)."
    ),
    "tasks_per_day": (
        "**Question 2 of 3 — Tasks per day**\n"
        "How many separate tasks on each **active** day? Send one number from **1** to **5** (example: `2`)."
    ),
    "skip_days": (
        "**Question 3 of 3 — Skip days**\n"
        "Which weekdays should have **no tasks**? "
        "Name them (e.g. **Saturday and Sunday**) or say **none** for every day."
    ),
    "difficulty": (
        "How hard should this feel? Reply with exactly one word: **easy**, **medium**, or **hard**."
    ),
    "minutes_per_day": (
        "How many minutes per day can you spend on this goal?\n"
        "Examples: `45`, `1 hour`, or `10 to 15 minutes`."
    ),
}

_WEEKDAY_PARSE: dict[str, int] = {
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

_WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

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


def question_for_step(step: StepId, session: dict[str, Any] | None = None) -> str:
    """Contextual intake question using goal title / description when available."""
    session = session or {}
    title = (session.get("title") or "").strip()
    desc = (session.get("description") or "").strip()
    goal_line = ""
    if title:
        goal_line = f"**Your goal:** {title}"
        if desc:
            goal_line += f"\n_{desc[:200]}{'…' if len(desc) > 200 else ''}_"

    if step == "objective" and title:
        base = (
            "**Question 1 of 3 — Your goal**\n"
            f"What do you want to achieve in the next 7 days for **{title}**? "
            "Be specific (e.g. measurable outcome or daily habit).\n"
            "_Reply **ok** to use your goal title as the objective, or write your own._"
        )
        return f"{goal_line}\n\n{base}" if goal_line else base

    if step == "tasks_per_day" and title:
        return (
            (f"{goal_line}\n\n" if goal_line else "")
            + f"**Question 2 of 3 — Tasks per day**\n"
            f"How many separate tasks per active day for **{title}**? "
            "Send one number from **1** to **5** (example: `2`)."
        )

    if step == "skip_days":
        return (
            (f"{goal_line}\n\n" if goal_line else "")
            + QUESTIONS["skip_days"]
        )

    return QUESTIONS.get(step, str(step))


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
    if step == "skip_days":
        return parse_skip_days(raw)
    if step == "minutes_per_day":
        return _parse_minutes_per_day(raw)
    return ParseResult(valid=False, hint="Unknown question step.")


def parse_skip_days(raw: str) -> ParseResult:
    text = (raw or "").strip()
    if not text:
        return ParseResult(valid=False, hint="Say which days to skip, or **none** for every day.")
    lower = text.lower()
    if re.search(
        r"\b(?:none|no skip|no days|every day|all days|all week|7 days|don't skip|do not skip)\b",
        lower,
    ):
        return ParseResult(valid=True, parsed=[], display="No skip days (tasks every day)")
    if re.search(r"\bweekends?\b", lower):
        return ParseResult(valid=True, parsed=[5, 6], display="Skip Saturday & Sunday")
    if re.search(r"\bweekdays?\b", lower) and "weekend" not in lower:
        return ParseResult(valid=True, parsed=[0, 1, 2, 3, 4], display="Skip Monday–Friday")
    found: set[int] = set()
    for name, dow in _WEEKDAY_PARSE.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            found.add(dow)
    if found:
        labels = ", ".join(_WEEKDAY_LABELS[d] for d in sorted(found))
        return ParseResult(
            valid=True,
            parsed=sorted(found),
            display=f"Skip {labels}",
        )
    return ParseResult(
        valid=False,
        hint="Name weekdays to skip (e.g. **Monday and Sunday**) or say **none**.",
    )


def format_skip_days(parsed: list[int] | None) -> str:
    if not parsed:
        return "No skip days (tasks every day)"
    return "Skip " + ", ".join(_WEEKDAY_LABELS[d] for d in sorted(parsed))


def plan_minutes_per_day(answers: dict[str, Any]) -> int:
    slot = answers.get("minutes_per_day") or {}
    if slot.get("valid") and slot.get("parsed") is not None:
        try:
            return max(5, min(480, int(slot["parsed"])))
        except (TypeError, ValueError):
            pass
    return DEFAULT_PLAN_MINUTES


def plan_difficulty(answers: dict[str, Any]) -> str:
    slot = answers.get("difficulty") or {}
    diff = str(slot.get("parsed") or "medium").lower()
    return diff if diff in ("easy", "medium", "hard") else "medium"


def plan_skip_days(answers: dict[str, Any]) -> list[int]:
    slot = answers.get("skip_days") or {}
    if not slot.get("valid"):
        return []
    parsed = slot.get("parsed")
    if not isinstance(parsed, list):
        return []
    return [int(d) for d in parsed if isinstance(d, int) and 0 <= int(d) <= 6]


def ensure_plan_defaults(answers: dict[str, Any]) -> dict[str, Any]:
    """Fill internal defaults (difficulty, optional minutes) without extra user questions."""
    out = dict(answers)
    if not (out.get("difficulty") or {}).get("valid"):
        out["difficulty"] = {
            "valid": True,
            "parsed": "medium",
            "raw": "medium",
            "display": "medium",
        }
    return out


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
    lower = raw.lower()
    _WORD_NUMBERS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "a": 1,
        "an": 1,
    }
    for word, n in _WORD_NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return ParseResult(valid=True, parsed=n)
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
        hint="Send a whole number from **1 to 5** (example: 2 or *two tasks*).",
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


def confirmation_prompt(answers: dict[str, Any], *, goal_title: str = "") -> str:
    title_line = f"**Goal:** {goal_title.strip()}\n" if (goal_title or "").strip() else ""
    skip_label = _answer_label(answers, "skip_days")
    if not skip_label or skip_label == "[]":
        skip_label = format_skip_days(plan_skip_days(answers))
    return (
        "**Review your 7-day plan settings**\n"
        f"{title_line}"
        f"1. Objective: {_answer_label(answers, 'objective')}\n"
        f"2. Tasks per active day: {_answer_label(answers, 'tasks_per_day')}\n"
        f"3. Skip days: {skip_label}\n\n"
        "Reply **yes** to create tasks in your calendar.\n"
        "To change something, say e.g. **2 tasks per day** or **skip Monday and Sunday**."
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
    if re.match(r"^okay[,.!\s]*$", t):
        return "yes"
    if t in ("no", "n", "wait", "stop", "cancel"):
        return "no"
    if re.search(r"\b(yes|yeah|yep|go ahead|looks good)\b", t):
        return "yes"
    return "unclear"


def try_apply_confirm_edits(
    message: str,
    answers: dict[str, Any],
    *,
    default_objective: str = "",
) -> tuple[dict[str, Any], str | None]:
    """
    Apply inline corrections during confirm (e.g. '1 task per day', 'change difficulty to hard').
    Returns (updated answers, short ack) or unchanged if nothing parsed.
    """
    text = (message or "").strip()
    if not text:
        return answers, None

    acks: list[str] = []
    lower = text.lower()

    if re.search(r"\b(skip|skipping|weekend|weekday)\b", lower) or any(
        re.search(rf"\b{re.escape(name)}\b", lower) for name in _WEEKDAY_PARSE
    ):
        r = parse_skip_days(text)
        if r.valid:
            answers["skip_days"] = {
                "valid": True,
                "parsed": r.parsed,
                "raw": text,
                "display": r.display or format_skip_days(r.parsed if isinstance(r.parsed, list) else []),
            }
            acks.append(f"skip days → {answers['skip_days']['display']}")

    if re.search(r"\b(objective|goal)\b", lower) or (
        len(text) > 12
        and "task" not in lower
        and "skip" not in lower
        and not any(re.search(rf"\b{re.escape(name)}\b", lower) for name in _WEEKDAY_PARSE)
        and "minute" not in lower
        and "difficult" not in lower
    ):
        r = parse_answer("objective", text, default_objective=default_objective)
        if r.valid:
            answers["objective"] = {
                "valid": True,
                "parsed": r.parsed,
                "raw": text,
                "display": r.display or str(r.parsed)[:80],
            }
            acks.append(f"objective → {answers['objective']['display']}")

    if re.search(r"\b(task|tasks)\b.*\b(day|daily|per)\b|\bper day\b", lower) or re.search(
        r"\b\d\b.*\btask", lower
    ):
        r = parse_answer("tasks_per_day", text)
        if r.valid:
            answers["tasks_per_day"] = {
                "valid": True,
                "parsed": r.parsed,
                "raw": text,
                "display": r.display or str(r.parsed),
            }
            acks.append(f"tasks/day → **{answers['tasks_per_day']['display']}**")

    if re.search(r"\b(minute|hour|time|mins?)\b", lower) or re.search(r"\b\d+\s*h", lower):
        r = parse_answer("minutes_per_day", text)
        if r.valid:
            answers["minutes_per_day"] = {
                "valid": True,
                "parsed": r.parsed,
                "raw": text,
                "display": r.display or f"{r.parsed} minutes per day",
            }
            acks.append(f"time/day → {answers['minutes_per_day']['display']}")

    if re.search(r"\b(easy|medium|hard|difficult|intensity)\b", lower):
        r = parse_answer("difficulty", text)
        if r.valid:
            answers["difficulty"] = {
                "valid": True,
                "parsed": r.parsed,
                "raw": text,
                "display": r.display or str(r.parsed),
            }
            acks.append(f"difficulty → **{answers['difficulty']['display']}**")

    if not acks:
        return answers, None
    return answers, "; ".join(acks)


def is_confirm_settings_edit(message: str, *, default_objective: str = "") -> bool:
    """True when confirm-phase message adjusts plan settings (not yes/no/delete)."""
    if parse_confirmation(message) in ("yes", "no"):
        return False
    lower = (message or "").strip().lower()
    if re.search(
        r"\b(delete|remove|clear|drop|cancel)\b.*\b(goal|plan|tasks?)\b|"
        r"\b(goal|plan|tasks?)\b.*\b(delete|remove|clear|drop)\b",
        lower,
    ):
        return False
    _, ack = try_apply_confirm_edits(
        message,
        {},
        default_objective=default_objective or "7-day goal target",
    )
    return ack is not None
