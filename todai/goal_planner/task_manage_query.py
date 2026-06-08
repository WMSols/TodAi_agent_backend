"""Parse delete/manage intents for goal tasks (Groq normalize + static verify)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from todai.agent.planner.groq_config import GROQ_API_KEY
from todai.agent.planner.llm import groq_chat_json
from todai.goal_planner.task_query import (
    _DELETE_VERB,
    _HAS_TASK_ORDINAL,
    _match_tasks_by_title,
    filter_tasks_by_dates,
    parse_day_dates_in_message,
    resolve_task_ordinal,
    resolve_task_ordinals,
)

logger = logging.getLogger(__name__)

DeleteAction = Literal[
    "none",
    "delete_day",
    "delete_task",
    "delete_plan",
    "delete_goal",
    "delete_all",
    "clarify",
]

_DELETE_ALL = re.compile(
    r"\b(?:delete|remove|clear)\b.*\b(?:all|every)\b.*\b(?:goals?|plans?)\b|"
    r"\b(?:delete|remove|clear)\b.*\b(?:my\s+)?goals\b",
    re.I,
)
_DELETE_GOAL = re.compile(
    r"\b(?:delete|remove|cancel|clear|drop|discard|abort)\b.*\b(?:my\s+)?goal\b|"
    r"\b(?:my\s+)?goal\b.*\b(?:delete|remove|discard|drop)\b",
    re.I,
)
_DELETE_PLAN_WEEK = re.compile(
    r"\b(?:delete|remove|clear)\b.*\b(?:tasks?\s+only|only\s+tasks?)\b|"
    r"\b(?:delete|remove|clear)\b.*\b(?:all|entire|whole|full)\s+(?:week|plan)\s*tasks?\b|"
    r"\breset\b.*\b(?:plan|tasks?|draft)\b|"
    r"\b(?:delete|remove)\b.*\bdraft\b|"
    r"\b(?:delete|remove)\b.*\b(?:7[- ]?day|week)\s+plan\b",
    re.I,
)
_DELETE_TASKS_VAGUE = re.compile(
    r"\b(?:delete|remove|clear|drop)\b.*\b(?:my\s+)?tasks?\b|"
    r"\b(?:my\s+)?tasks?\b.*\b(?:delete|remove|clear|drop)\b",
    re.I,
)
_HAS_WEEKDAY = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.I,
)


@dataclass(frozen=True)
class DeleteManageIntent:
    action: DeleteAction
    dates: tuple[str, ...] = ()
    day_label: str = ""
    tasks: tuple[dict[str, Any], ...] = ()
    clarify_message: str = ""


def _day_label(dates: list[date]) -> str:
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0].strftime("%A, %d %b")
    return ", ".join(d.strftime("%a %d %b") for d in dates)


def _no_tasks_on_days_message(
    empty_days: list[date],
    all_tasks: list[dict[str, Any]],
) -> str:
    labels = _day_label(empty_days)
    occupied: list[date] = []
    seen: set[str] = set()
    for row in all_tasks:
        raw = str(row.get("task_date", ""))[:10]
        if not raw or raw in seen:
            continue
        try:
            occupied.append(date.fromisoformat(raw))
            seen.add(raw)
        except ValueError:
            continue
    occupied.sort()
    if occupied:
        sched = ", ".join(d.strftime("%a %d %b") for d in occupied)
        return (
            f"No tasks on **{labels}** in this plan. "
            f"Tasks are scheduled on: **{sched}**."
        )
    return f"No tasks on **{labels}** in this plan."


_DELETE_GROQ_SYSTEM = (
    "Delete/manage intent ONLY when user wants to remove something.\n"
    "JSON: "
    '{"status":"ok"|"none","action":"delete_day"|"delete_task"|"delete_plan"|'
    '"delete_goal"|"delete_all"|"clarify","dates":["YYYY-MM-DD"],'
    '"taskOrdinal":null|int,"taskOrdinals":[int],"taskTitleHint":null|string,'
    '"clarifyMessage":string}\n\n'
    "status=none for setup answers (skip days, tasks/day, none) — NOT delete.\n"
    "**DO NOT INVENT** dates, weekdays, or tasks. If a day has no tasks in tasks_by_date, do not list any.\n"
    "dates MUST be YYYY-MM-DD keys from days_in_plan / tasks_by_date ONLY — never invent dates.\n"
    "DATE RULES (critical):\n"
    "- User names ONE weekday (e.g. Monday, Mon) → dates = ONLY that weekday's date in days_in_plan.\n"
    "- User names TWO weekdays → dates = exactly those two dates, nothing else.\n"
    "- 'Monday tasks' / 'delete Monday' → ONE date (the Monday in the plan), NOT the whole week.\n"
    "- delete_plan / delete_all / whole week → action delete_plan or delete_all, dates=[].\n"
    "- delete_task + one ordinal/title → one task; multiple ordinals (2nd and 3rd) → taskOrdinals [2,3].\n"
    "- Plural 'tasks for Wednesday' with NO ordinals → delete_day (all tasks that day), NOT taskOrdinal 1.\n"
    "- 'all tasks for/on Wednesday' → delete_day.\n"
    "- If ambiguous which day → action=clarify, dates=[].\n"
    "Never add dates the user did not name. Never delete every day when one weekday was named.\n"
    "taskOrdinal / taskOrdinals are 1-based within that day's task list order."
)


def _asks_all_day_tasks(message: str) -> bool:
    """Plural tasks on a weekday with no specific task number → delete whole day."""
    text = (message or "").strip()
    if not text or not _HAS_WEEKDAY.search(text):
        return False
    if _HAS_TASK_ORDINAL.search(text):
        return False
    if re.search(r"\ball\s+tasks?\b", text, re.I):
        return True
    return bool(re.search(r"\btasks\b", text, re.I))


def _dedupe_tasks(tasks: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for t in tasks:
        tid = str(t.get("id") or "")
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        out.append(t)
    return tuple(out)


def parse_delete_manage_intent(
    message: str,
    *,
    start: date,
    end: date,
    all_tasks: list[dict[str, Any]],
    allow_groq: bool = True,
) -> DeleteManageIntent:
    text = (message or "").strip()
    if not text:
        return DeleteManageIntent(action="none")

    if allow_groq and GROQ_API_KEY:
        groq_raw = _groq_delete_intent(text, start=start, end=end, all_tasks=all_tasks)
        if groq_raw is not None:
            status = str(groq_raw.get("status") or "").lower().strip()
            if status != "none":
                verified = verify_delete_manage_intent(
                    groq_raw, start=start, end=end, all_tasks=all_tasks, raw_message=text
                )
                if verified.action != "none":
                    return verified

    return _parse_delete_manage_intent_static(text, start=start, end=end, all_tasks=all_tasks)


def verify_delete_manage_intent(
    groq_raw: dict[str, Any],
    *,
    start: date,
    end: date,
    all_tasks: list[dict[str, Any]],
    raw_message: str = "",
) -> DeleteManageIntent:
    """Static verification of Groq-normalized delete intent before any write."""
    status = str(groq_raw.get("status") or "").lower().strip()
    if status == "none":
        return DeleteManageIntent(action="none")

    action = str(groq_raw.get("action") or "none").lower().strip()
    valid_actions = (
        "delete_day",
        "delete_task",
        "delete_plan",
        "delete_goal",
        "delete_all",
        "clarify",
        "none",
    )
    if action not in valid_actions:
        return DeleteManageIntent(action="none")

    if action == "clarify":
        msg = str(groq_raw.get("clarifyMessage") or groq_raw.get("clarify_message") or "").strip()
        if not msg:
            msg = (
                "Which tasks should I remove?\n\n"
                "• **One day** — e.g. *remove Tuesday tasks*\n"
                "• **One task** — e.g. *delete the first task on Friday*\n"
                "• **Whole week** — say *delete all plan tasks*\n"
                "• **Entire goal** — say *delete my goal*"
            )
        return DeleteManageIntent(action="clarify", clarify_message=msg[:800])

    if action in ("delete_all", "delete_goal", "delete_plan"):
        if action == "delete_plan" and not all_tasks:
            return DeleteManageIntent(action="delete_goal")
        return DeleteManageIntent(action=action)  # type: ignore[arg-type]

    groq_dates = _verify_dates(groq_raw.get("dates"), start=start, end=end)
    msg_dates = (
        parse_day_dates_in_message(raw_message, start=start, end=end) if raw_message else []
    )
    if msg_dates and action in ("delete_day", "delete_task"):
        day_dates = msg_dates
    else:
        day_dates = groq_dates
        if not day_dates and msg_dates:
            day_dates = msg_dates

    if action == "delete_day":
        if not day_dates:
            return DeleteManageIntent(action="clarify", clarify_message=_clarify_which_day())
        dates_with_tasks: list[date] = []
        dates_without_tasks: list[date] = []
        for d in day_dates:
            if filter_tasks_by_dates(all_tasks, (d.isoformat(),)):
                dates_with_tasks.append(d)
            else:
                dates_without_tasks.append(d)
        if not dates_with_tasks:
            return DeleteManageIntent(
                action="clarify",
                clarify_message=_no_tasks_on_days_message(dates_without_tasks, all_tasks),
            )
        iso = tuple(d.isoformat() for d in dates_with_tasks)
        day_tasks = filter_tasks_by_dates(all_tasks, iso)
        return DeleteManageIntent(
            action="delete_day",
            dates=iso,
            day_label=_day_label(dates_with_tasks),
            tasks=tuple(day_tasks),
        )

    if action == "delete_task":
        if raw_message and _asks_all_day_tasks(raw_message):
            day_dates = msg_dates or day_dates
            if day_dates:
                iso = tuple(d.isoformat() for d in day_dates)
                day_tasks = filter_tasks_by_dates(all_tasks, iso)
                if day_tasks:
                    return DeleteManageIntent(
                        action="delete_day",
                        dates=iso,
                        day_label=_day_label(day_dates),
                        tasks=tuple(day_tasks),
                    )
        iso = tuple(d.isoformat() for d in day_dates) if day_dates else ()
        scope_tasks = filter_tasks_by_dates(all_tasks, iso) if iso else list(all_tasks)
        ordered = sorted(scope_tasks, key=lambda x: int(x.get("sort_order") or 0))
        picked: list[dict[str, Any]] = []
        if raw_message:
            picked = resolve_task_ordinals(raw_message, scope_tasks)
            if not picked:
                one = resolve_task_ordinal(raw_message, scope_tasks)
                if one:
                    picked = [one]
        if not picked:
            ordinals_raw = groq_raw.get("taskOrdinals") or groq_raw.get("task_ordinals")
            if isinstance(ordinals_raw, list):
                for o in ordinals_raw:
                    if isinstance(o, (int, float)) and int(o) >= 1:
                        idx = int(o) - 1
                        if 0 <= idx < len(ordered):
                            picked.append(ordered[idx])
        if not picked:
            ordinal = groq_raw.get("taskOrdinal")
            if ordinal is None and "task_ordinal" in groq_raw:
                ordinal = groq_raw.get("task_ordinal")
            if isinstance(ordinal, (int, float)) and int(ordinal) >= 1:
                idx = int(ordinal) - 1
                if 0 <= idx < len(ordered):
                    picked.append(ordered[idx])
        if not picked:
            title_hint = groq_raw.get("taskTitleHint") or groq_raw.get("task_title_hint")
            if isinstance(title_hint, str) and title_hint.strip():
                matched = _match_tasks_by_title(title_hint, scope_tasks or all_tasks)
                if matched:
                    picked = [matched[0]]
        if not picked and scope_tasks:
            matched = _match_tasks_by_title(raw_message, scope_tasks)
            if matched:
                picked = [matched[0]]
        tasks_tuple = _dedupe_tasks(picked)
        if tasks_tuple:
            first = tasks_tuple[0]
            return DeleteManageIntent(
                action="delete_task",
                dates=iso or (str(first.get("task_date", ""))[:10],),
                day_label=_day_label(day_dates) if day_dates else "",
                tasks=tasks_tuple,
            )
        return DeleteManageIntent(action="clarify", clarify_message=_clarify_which_task())

    return DeleteManageIntent(action="none")


def _clarify_which_day() -> str:
    return (
        "Which day should I remove tasks from?\n\n"
        "Example: *remove Tuesday tasks* or *delete Friday*."
    )


def _clarify_which_task() -> str:
    return (
        "Which task should I remove?\n\n"
        "Example: *delete the first task on Friday* or *remove the squats task*."
    )


def _verify_dates(raw_dates: Any, *, start: date, end: date) -> list[date]:
    if not isinstance(raw_dates, list):
        return []
    out: list[date] = []
    for item in raw_dates:
        try:
            d = date.fromisoformat(str(item)[:10])
        except ValueError:
            continue
        if start <= d <= end:
            out.append(d)
    return sorted(set(out))


def _groq_delete_intent(
    message: str,
    *,
    start: date,
    end: date,
    all_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_day: dict[str, list[str]] = {}
    for t in all_tasks:
        d = str(t.get("task_date", ""))[:10]
        if not d:
            continue
        by_day.setdefault(d, []).append((t.get("title") or "Task").strip()[:60])
    days_in_plan: list[dict[str, Any]] = []
    for iso in sorted(by_day.keys()):
        try:
            wd = date.fromisoformat(iso).strftime("%A")
        except ValueError:
            wd = ""
        days_in_plan.append(
            {"date": iso, "weekday": wd, "task_titles": by_day[iso][:8]}
        )
    payload = {
        "user_message": message[:500],
        "plan_start": start.isoformat(),
        "plan_end": end.isoformat(),
        "days_in_plan": days_in_plan,
        "tasks_by_date": {k: v[:8] for k, v in sorted(by_day.items())},
        "total_tasks": len(all_tasks),
    }
    try:
        raw = groq_chat_json(
            [
                {"role": "system", "content": _DELETE_GROQ_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            phase="goal_delete_normalize",
            max_tokens=160,
            temperature=0,
        )
    except Exception as e:
        logger.warning("goal delete normalize Groq failed: %s", e)
        return None
    return raw if isinstance(raw, dict) else None


def _parse_delete_manage_intent_static(
    text: str,
    *,
    start: date,
    end: date,
    all_tasks: list[dict[str, Any]],
) -> DeleteManageIntent:
    if not _DELETE_VERB.search(text):
        return DeleteManageIntent(action="none")

    if _DELETE_ALL.search(text):
        return DeleteManageIntent(action="delete_all")

    if _DELETE_GOAL.search(text) and not _HAS_WEEKDAY.search(text):
        return DeleteManageIntent(action="delete_goal")

    day_dates = parse_day_dates_in_message(text, start=start, end=end)
    if day_dates:
        iso = tuple(d.isoformat() for d in day_dates)
        day_tasks = filter_tasks_by_dates(all_tasks, iso)
        if _asks_all_day_tasks(text):
            return DeleteManageIntent(
                action="delete_day",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=tuple(day_tasks),
            )
        multi = resolve_task_ordinals(text, day_tasks)
        if multi:
            return DeleteManageIntent(
                action="delete_task",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=_dedupe_tasks(multi),
            )
        one = resolve_task_ordinal(text, day_tasks)
        if one:
            return DeleteManageIntent(
                action="delete_task",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=(one,),
            )
        if day_tasks and (_DELETE_TASKS_VAGUE.search(text) or _HAS_WEEKDAY.search(text)):
            return DeleteManageIntent(
                action="delete_day",
                dates=iso,
                day_label=_day_label(day_dates),
                tasks=tuple(day_tasks),
            )

    matched = _match_tasks_by_title(text, all_tasks)
    if matched and len(matched) <= 3:
        return DeleteManageIntent(
            action="delete_task",
            tasks=tuple(matched[:1]),
        )

    if _DELETE_PLAN_WEEK.search(text) and not _HAS_WEEKDAY.search(text):
        if not all_tasks:
            return DeleteManageIntent(action="delete_goal")
        return DeleteManageIntent(action="delete_plan")

    if _DELETE_TASKS_VAGUE.search(text):
        if not all_tasks:
            return DeleteManageIntent(action="delete_goal")
        return DeleteManageIntent(
            action="clarify",
            clarify_message=(
                "Which tasks should I remove?\n\n"
                "• **One day** — e.g. *remove Tuesday tasks*\n"
                "• **One task** — e.g. *delete the first task on Friday*\n"
                "• **Whole week** — say *delete all plan tasks* or *reset my week*\n"
                "• **Entire goal** — say *delete my goal*"
            ),
        )

    if _DELETE_VERB.search(text):
        return DeleteManageIntent(action="delete_goal")

    return DeleteManageIntent(action="none")


parse_delete_manage_intent_static = _parse_delete_manage_intent_static
