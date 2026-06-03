"""Generate progressive, objective-specific goal tasks via Groq (per-day batches)."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.api.middleware.rate_limit import current_turn_user_id, groq_tracker

logger = logging.getLogger(__name__)

_DESC_RULE = (
    "Each description: two sentences, max 25 words. "
    "Tasks must be specific steps toward the objective — never generic placeholders."
)

_DAY_SYSTEM = (
    "You design one day of a multi-day goal plan. Build a stepwise curriculum: "
    "earlier days = foundations, later days = harder practice toward the week outcome. "
    + _DESC_RULE
    + " Return JSON only: {\"tasks\": [{\"title\": string, \"description\": string}, ...]} "
    "Same count and order as input slots. No markdown."
)

# Detect old rotating templates so we never silently accept them.
_GENERIC_TITLE_MARKERS = (
    "study and notes",
    "hands-on practice",
    "build or review",
    "light cardio warm-up",
    "strength or core block",
    "active recovery",
    "focused work block",
    "review and plan",
    "practice session",
)


@dataclass(frozen=True)
class GoalTaskGenerationError:
    code: str  # no_api_key | rate_limited | groq_failed | invalid_response | generic_template
    message: str
    retry_after_seconds: float = 0.0
    limit_hit: str | None = None

    def user_reply(self, usage: dict[str, Any] | None = None) -> str:
        if self.code == "rate_limited":
            wait = int(self.retry_after_seconds or (usage or {}).get("retry_after_seconds") or 30)
            hit = (self.limit_hit or "TPM/RPM").upper()
            return (
                f"**Could not generate your task plan** — Groq limit ({hit}). "
                f"Wait about **{wait} seconds** for the minute quota to refresh, then reply **yes** again "
                "to build tasks. Your settings are saved."
            )
        if self.code == "no_api_key":
            return (
                "**Could not generate tasks** — Groq API key is not configured. "
                "Set GROQ_API_KEY and try again."
            )
        return (
            f"**Could not generate your task plan** — {self.message} "
            "Please try again in a moment (reply **yes** on the review step)."
        )


def enrich_tasks_with_descriptions(
    *,
    objective: str,
    difficulty: str,
    tasks: list[dict[str, Any]],
    minutes_per_day: int,
    tasks_per_day: int,
    plan_start: date | None = None,
) -> tuple[list[dict[str, Any]], GoalTaskGenerationError | None]:
    """
    Fill task title/description via Groq (per day). No static template fallback.
    Returns (tasks, None) on success or (unchanged tasks, error) on failure.
    """
    if not tasks:
        return tasks, None
    if not GROQ_API_KEY:
        return tasks, GoalTaskGenerationError(
            code="no_api_key",
            message="missing API key",
        )

    per_task = max(5, minutes_per_day // max(1, tasks_per_day))
    by_day = _group_tasks_by_date(tasks)
    n_days = len(by_day)
    planned_tokens = min(6000, n_days * max(400, 120 * tasks_per_day) + 300)
    gate = groq_tracker.check_turn_allowed(
        planned_requests=n_days,
        planned_tokens=planned_tokens,
    )
    if not gate.allowed:
        usage = groq_tracker.usage_snapshot(current_turn_user_id())
        return tasks, GoalTaskGenerationError(
            code="rate_limited",
            message=gate.message,
            retry_after_seconds=gate.retry_after_seconds,
            limit_hit=gate.limit_hit,
        )

    specs, err = _generate_progressive_specs(
        objective=objective,
        difficulty=difficulty,
        by_day=by_day,
        per_task_mins=per_task,
        plan_start=plan_start,
    )
    if err:
        return tasks, err

    out: list[dict[str, Any]] = []
    flat_specs = [s for day_specs in specs for s in day_specs]
    if len(flat_specs) != len(tasks):
        return tasks, GoalTaskGenerationError(
            code="invalid_response",
            message=f"expected {len(tasks)} tasks, got {len(flat_specs)}",
        )

    for row, spec in zip(tasks, flat_specs):
        merged = dict(row)
        merged["title"] = (spec.get("title") or "Goal task")[:120]
        merged["description"] = (spec.get("description") or "")[:500]
        out.append(merged)
    return out, None


def _group_tasks_by_date(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        by_date[str(t.get("task_date") or "")[:10]].append(t)
    ordered_dates = sorted(by_date.keys())
    return [
        sorted(by_date[d], key=lambda x: int(x.get("sort_order") or 0))
        for d in ordered_dates
    ]


def _generate_progressive_specs(
    *,
    objective: str,
    difficulty: str,
    by_day: list[list[dict[str, Any]]],
    per_task_mins: int,
    plan_start: date | None,
) -> tuple[list[list[dict[str, str]]], GoalTaskGenerationError | None]:
    kind = _objective_kind(objective)
    total = len(by_day)
    prior_topics: list[str] = []
    all_days: list[list[dict[str, str]]] = []

    for day_idx, day_tasks in enumerate(by_day, start=1):
        day_date = str(day_tasks[0].get("task_date") or "")[:10] if day_tasks else ""
        slots = [
            {
                "order": int(t.get("sort_order") or 0) + 1,
                "minutes": per_task_mins,
                "flexible": not t.get("start_time"),
            }
            for t in day_tasks
        ]
        specs, err = _groq_specs_for_day(
            objective=objective,
            difficulty=difficulty,
            domain=kind,
            day_index=day_idx,
            total_days=total,
            day_date=day_date,
            slots=slots,
            prior_topics=prior_topics[-8:],
            plan_start=plan_start,
        )
        if err:
            return [], err
        if len(specs) != len(day_tasks):
            return [], GoalTaskGenerationError(
                code="invalid_response",
                message=f"day {day_idx}: expected {len(day_tasks)} tasks, got {len(specs)}",
            )
        for s in specs:
            if _is_generic_template_task(s.get("title", ""), s.get("description", "")):
                return [], GoalTaskGenerationError(
                    code="generic_template",
                    message="model returned generic placeholders; retry",
                )
        prior_topics.extend(s.get("title", "")[:60] for s in specs if s.get("title"))
        all_days.append(specs)

    return all_days, None


def _groq_specs_for_day(
    *,
    objective: str,
    difficulty: str,
    domain: str,
    day_index: int,
    total_days: int,
    day_date: str,
    slots: list[dict[str, Any]],
    prior_topics: list[str],
    plan_start: date | None,
) -> tuple[list[dict[str, str]], GoalTaskGenerationError | None]:
    ramp = _difficulty_ramp(day_index, total_days)
    user_payload = {
        "objective": objective,
        "difficulty": difficulty,
        "domain": domain,
        "day_index": day_index,
        "total_days": total_days,
        "day_date": day_date,
        "intensity": ramp,
        "prior_days_focus": prior_topics,
        "slots": slots,
        "rules": (
            f"Day {day_index}/{total_days}: teach the next step toward the objective. "
            "Do not repeat prior days verbatim. Increase challenge toward the end of the week."
        ),
    }
    max_tokens = min(900, 120 + 90 * len(slots))

    raw = groq_chat_json(
        [
            {"role": "system", "content": _DAY_SYSTEM},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        phase="goal_task_gen",
        max_tokens=max_tokens,
        temperature=0.4,
    )

    err = _error_from_groq_raw(raw)
    if err:
        return [], err

    tasks_out = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks_out, list):
        return [], GoalTaskGenerationError(
            code="invalid_response",
            message=f"day {day_index}: missing tasks array",
        )

    specs: list[dict[str, str]] = []
    for item in tasks_out:
        if not isinstance(item, dict):
            return [], GoalTaskGenerationError(
                code="invalid_response",
                message=f"day {day_index}: bad task entry",
            )
        title = str(item.get("title") or "").strip()
        desc = _trim_description(str(item.get("description") or "").strip())
        if not title or not desc:
            return [], GoalTaskGenerationError(
                code="invalid_response",
                message=f"day {day_index}: empty title or description",
            )
        specs.append({"title": title, "description": desc})
    return specs, None


def _error_from_groq_raw(raw: dict[str, Any]) -> GoalTaskGenerationError | None:
    dbg = raw.get("_groq_debug") if isinstance(raw, dict) else {}
    if isinstance(dbg, dict) and dbg.get("rate_limited"):
        usage = raw.get("_api_usage") if isinstance(raw.get("_api_usage"), dict) else {}
        return GoalTaskGenerationError(
            code="rate_limited",
            message=str(raw.get("replyText") or "rate limited"),
            retry_after_seconds=float(usage.get("retry_after_seconds") or 30),
            limit_hit=str(dbg.get("limit_hit") or usage.get("limit_hit") or "rpm"),
        )
    if raw.get("replyText") and not raw.get("tasks"):
        text = str(raw.get("replyText") or "")
        if "rate limit" in text.lower() or "wait" in text.lower():
            usage = raw.get("_api_usage") if isinstance(raw.get("_api_usage"), dict) else {}
            return GoalTaskGenerationError(
                code="rate_limited",
                message=text,
                retry_after_seconds=float(usage.get("retry_after_seconds") or 60),
                limit_hit="rpm",
            )
        return GoalTaskGenerationError(code="groq_failed", message=text[:200])
    if not isinstance(raw, dict) or "tasks" not in raw:
        preview = str(dbg.get("raw_preview") or raw.get("replyText") or "invalid JSON")[:120]
        return GoalTaskGenerationError(code="groq_failed", message=preview)
    return None


def _difficulty_ramp(day_index: int, total_days: int) -> str:
    if total_days <= 1:
        return "peak"
    ratio = day_index / total_days
    if ratio <= 0.35:
        return "foundation"
    if ratio <= 0.7:
        return "building"
    return "stretch"


def _is_generic_template_task(title: str, description: str) -> bool:
    t = title.lower()
    for marker in _GENERIC_TITLE_MARKERS:
        if marker in t:
            return True
    if description.lower().startswith("read or watch core material"):
        return True
    return False


def _objective_kind(objective: str) -> str:
    o = (objective or "").lower()
    if re.search(
        r"\b(python|code|coding|program|learn|study|read|course|tutorial|chapter|"
        r"exercise|homework|language|developer|software)\b",
        o,
    ):
        return "learning"
    if re.search(
        r"\b(weight|loss|lose|fitness|workout|gym|cardio|run|jog|cycle|cycling|"
        r"walk|steps|calories|muscle)\b",
        o,
    ):
        return "fitness"
    return "general"


def _trim_description(desc: str) -> str:
    if not desc:
        return ""
    words = desc.split()
    if len(words) <= 25:
        return desc
    return " ".join(words[:25]).rstrip(".,;") + "."
