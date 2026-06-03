"""Goal planner read/write tools (list, review, delete)."""

from __future__ import annotations

from typing import Any

from todai.goal_planner.session_store import GoalPlanSessionStore


def execute_list_goals(store: GoalPlanSessionStore) -> dict[str, Any]:
    plans = store.list_user_plans()
    goals = store.list_user_goals()
    return {"ok": True, "plans": plans, "goals": goals}


def execute_list_goals_with_progress(
    store: GoalPlanSessionStore,
    *,
    current_plan_id: str | None = None,
) -> dict[str, Any]:
    goals = store.list_user_goals()
    plans = []
    for p in store.list_user_plans():
        pid = str(p.get("id", ""))
        tasks = store.list_goal_tasks(pid) if pid else []
        done = sum(1 for t in tasks if (t.get("status") or "").lower() in ("done", "completed"))
        skipped = sum(1 for t in tasks if (t.get("status") or "").lower() in ("skipped", "cancelled"))
        total = len(tasks)
        pending = total - done - skipped
        percent = int((done / total) * 100) if total else 0
        plans.append(
            {
                **p,
                "is_current": pid == current_plan_id,
                "progress": {
                    "total": total,
                    "done": done,
                    "pending": pending,
                    "skipped": skipped,
                    "percent": percent,
                },
            }
        )
    return {"ok": True, "goals": goals, "plans": plans}


def execute_delete_plan(store: GoalPlanSessionStore, plan_id: str) -> dict[str, Any]:
    removed = store.delete_plan(plan_id)
    return {"ok": True, **removed}


def execute_delete_goal(store: GoalPlanSessionStore, plan_id: str) -> dict[str, Any]:
    removed = store.delete_goal_for_plan(plan_id)
    return removed


def execute_delete_all_goals(store: GoalPlanSessionStore) -> dict[str, Any]:
    return store.delete_all_user_goal_data()
