"""Generate actionable goal task titles and descriptions (Groq + template fallback)."""

from __future__ import annotations

import json
import logging
from typing import Any

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json

logger = logging.getLogger(__name__)

_FALLBACK_ACTIVITIES: list[tuple[str, str]] = [
    (
        "Light cardio warm-up",
        "Easy jog, brisk walk, or cycle for {mins} minutes. Keep effort conversational.",
    ),
    (
        "Strength or core block",
        "Bodyweight circuit or light weights for {mins} minutes. Focus on form.",
    ),
    (
        "Active recovery",
        "Stretching, yoga, or a gentle walk for {mins} minutes.",
    ),
]


def enrich_tasks_with_descriptions(
    *,
    objective: str,
    difficulty: str,
    tasks: list[dict[str, Any]],
    minutes_per_day: int,
    tasks_per_day: int,
) -> list[dict[str, Any]]:
    if not tasks:
        return tasks
    per_task = max(5, minutes_per_day // max(1, tasks_per_day))
    specs = _groq_task_specs(objective, difficulty, tasks, per_task)
    if not specs or len(specs) != len(tasks):
        specs = _template_specs(objective, difficulty, len(tasks), per_task)
    out: list[dict[str, Any]] = []
    for row, spec in zip(tasks, specs):
        merged = dict(row)
        merged["title"] = (spec.get("title") or row.get("title") or "Goal task")[:120]
        merged["description"] = (spec.get("description") or row.get("description") or "")[:500]
        out.append(merged)
    return out


def _groq_task_specs(
    objective: str,
    difficulty: str,
    tasks: list[dict[str, Any]],
    per_task_mins: int,
) -> list[dict[str, str]] | None:
    if not GROQ_API_KEY:
        return None
    brief = [
        {
            "day": t.get("task_date"),
            "order": int(t.get("sort_order") or 0) + 1,
            "minutes": per_task_mins,
            "flexible": not t.get("start_time"),
        }
        for t in tasks
    ]
    system = (
        "You create specific, actionable daily tasks for a 7-day personal goal plan. "
        "Return JSON only: {\"tasks\": [{\"title\": \"...\", \"description\": \"...\"}, ...]} "
        "with exactly one entry per input slot, same order. "
        "Descriptions are 1-2 sentences: what to do, how long, and difficulty cue. "
        "No markdown."
    )
    user = json.dumps(
        {
            "objective": objective,
            "difficulty": difficulty,
            "slots": brief,
        },
        ensure_ascii=False,
    )
    try:
        raw = groq_chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            phase="goal_task_gen",
            max_tokens=1200,
            temperature=0.35,
        )
        tasks_out = raw.get("tasks") if isinstance(raw, dict) else None
        if not isinstance(tasks_out, list):
            return None
        specs: list[dict[str, str]] = []
        for item in tasks_out:
            if not isinstance(item, dict):
                return None
            specs.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                }
            )
        return specs if len(specs) == len(tasks) else None
    except Exception as e:
        logger.warning("goal task Groq generation failed: %s", e)
        return None


def _template_specs(
    objective: str,
    difficulty: str,
    count: int,
    per_task_mins: int,
) -> list[dict[str, str]]:
    obj = objective.strip() or "your goal"
    obj_key = obj.lower()
    specs: list[dict[str, str]] = []
    for i in range(count):
        tpl = _FALLBACK_ACTIVITIES[i % len(_FALLBACK_ACTIVITIES)]
        title, desc_tpl = tpl
        if "weight" in obj_key or "loss" in obj_key or "fit" in obj_key:
            if i % 3 == 0:
                title, desc_tpl = (
                    "Running warm-up",
                    "Light jog or fast walk for {mins} minutes. Stay at easy effort.",
                )
            elif i % 3 == 1:
                title, desc_tpl = (
                    "Core and posture work",
                    "Planks, bridges, and stretches for {mins} minutes.",
                )
        desc = desc_tpl.format(mins=per_task_mins)
        specs.append(
            {
                "title": f"{title} — {obj[:40]}",
                "description": f"{desc} Difficulty: {difficulty}.",
            }
        )
    return specs
