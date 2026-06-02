"""Goal planner read/write tools (list, review, delete)."""

from __future__ import annotations

from typing import Any

from todai.goal_planner.session_store import GoalPlanSessionStore


def execute_list_goals(store: GoalPlanSessionStore) -> dict[str, Any]:
    plans = store.list_user_plans()
    goals = store.list_user_goals()
    return {"ok": True, "plans": plans, "goals": goals}


def execute_delete_plan(store: GoalPlanSessionStore, plan_id: str) -> dict[str, Any]:
    removed = store.delete_plan(plan_id)
    return {"ok": True, **removed}


def execute_delete_all_goals(store: GoalPlanSessionStore) -> dict[str, Any]:
    return store.delete_all_user_goal_data()
