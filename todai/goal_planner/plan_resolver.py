"""Resolve which week plan a goal chat message refers to (UI hint + message text)."""

from __future__ import annotations

import re
from typing import Any

from todai.goal_planner.session_store import GoalPlanSessionStore

_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _plan_catalog(store: GoalPlanSessionStore) -> list[dict[str, Any]]:
    goals = {str(g.get("id")): g for g in store.list_user_goals()}
    catalog: list[dict[str, Any]] = []
    for p in store.list_user_plans():
        gid = str(p.get("goal_id") or "")
        g = goals.get(gid) or {}
        title = (g.get("title") or "").strip()
        notes = (p.get("plan_notes") or "").strip()
        catalog.append(
            {
                "plan_id": str(p.get("id") or ""),
                "goal_id": gid,
                "goal_title": title,
                "plan_notes": notes,
                "status": (p.get("status") or "draft").lower(),
                "start_date": str(p.get("start_date") or "")[:10],
                "end_date": str(p.get("end_date") or "")[:10],
            }
        )
    return catalog


def _score_plan(text: str, entry: dict[str, Any]) -> int:
    score = 0
    title = (entry.get("goal_title") or "").lower()
    notes = (entry.get("plan_notes") or "").lower()
    if title and len(title) >= 3 and title in text:
        score += 12
    elif title:
        words = [w for w in title.split() if len(w) >= 4]
        if words and sum(1 for w in words if w in text) >= min(2, len(words)):
            score += 8
    if notes and len(notes) >= 4 and notes[:40] in text:
        score += 6
    for d in _DATE_RE.findall(text):
        if d in entry.get("start_date", "") or d in entry.get("end_date", ""):
            score += 5
    if entry.get("start_date") and entry["start_date"] in text:
        score += 4
    if entry.get("end_date") and entry["end_date"] in text:
        score += 4
    if (entry.get("status") or "") == "active":
        score += 1
    return score


def resolve_plan_for_turn(
    store: GoalPlanSessionStore,
    message: str,
    preferred_plan_id: str,
) -> tuple[str, str]:
    """
    Pick plan_id for this turn. preferred_plan_id is the UI dropdown hint.

    Returns (plan_id, reason_code).
    """
    catalog = _plan_catalog(store)
    hint = (preferred_plan_id or "").strip()
    valid_ids = {e["plan_id"] for e in catalog if e["plan_id"]}

    if not catalog:
        return hint, "no_plans"

    if len(catalog) == 1:
        only = catalog[0]["plan_id"]
        if only == hint:
            return only, "single_plan"
        return only, "single_plan_override"

    text = (message or "").lower().strip()
    if not text:
        if hint and hint in valid_ids:
            return hint, "hint_default"
        return _default_plan_id(catalog), "default_no_message"

    # Explicit "all goals" — keep hint for manage list scope
    if re.search(r"\b(all|every)\b.*\bgoal", text) or re.search(r"\ball goals\b", text):
        if hint and hint in valid_ids:
            return hint, "all_goals_hint"
        return _default_plan_id(catalog), "all_goals_default"

    scores = [(e["plan_id"], _score_plan(text, e)) for e in catalog]
    scores.sort(key=lambda x: x[1], reverse=True)
    best_id, best_score = scores[0]
    hint_score = 0
    if hint and hint in valid_ids:
        hint_entry = next(e for e in catalog if e["plan_id"] == hint)
        hint_score = _score_plan(text, hint_entry)
        sess = store._load_plan_session(hint)
        phase = (sess.get("phase") or "").lower()
        if phase in ("interrogate", "confirm", "creating", "ready"):
            return hint, "intake_locked"

    # Strong match on another plan (active / manage chat)
    if best_score >= 8 and best_id != hint and best_score > hint_score + 2:
        return best_id, "message_match"

    if hint and hint in valid_ids:
        return hint, "hint"

    return _default_plan_id(catalog), "default"


def _default_plan_id(catalog: list[dict[str, Any]]) -> str:
    for e in catalog:
        if e.get("status") == "active":
            return e["plan_id"]
    return catalog[0]["plan_id"]


def plan_needs_task_setup(
    store: GoalPlanSessionStore,
    plan_id: str,
    session: dict[str, Any] | None = None,
) -> bool:
    """True when this week plan has no tasks and still needs intake or generation."""
    if not plan_id:
        return False
    if store.list_goal_tasks(plan_id):
        return False
    session = session or store._load_plan_session(plan_id) or {}
    phase = str(session.get("phase") or "interrogate").lower()
    if phase in ("interrogate", "confirm", "ready", "creating", "intake", ""):
        return True
    row = store.get_plan_row(plan_id) or {}
    return str(row.get("status") or "draft").lower() == "draft"
