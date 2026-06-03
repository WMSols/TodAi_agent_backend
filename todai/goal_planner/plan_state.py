"""Plan readiness helpers (draft / no tasks yet)."""

from __future__ import annotations

from typing import Any

from todai.goal_planner.session_store import GoalPlanSessionStore


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
