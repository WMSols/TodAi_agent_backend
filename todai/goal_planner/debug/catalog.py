"""Goal agent route + LLM prompt catalog for the debug UI."""

from __future__ import annotations

import importlib
from typing import Any

# phase id → (module path, constant name)
_PROMPT_SOURCES: dict[str, tuple[str, str]] = {
    "goal_router": ("todai.goal_planner.routing.router", "GOAL_ROUTER_SYSTEM"),
    "goal_intake_init": ("todai.goal_planner.ai_intake", "_INIT_SYSTEM"),
    "goal_intake_finalize": ("todai.goal_planner.ai_intake", "_FINALIZE_SYSTEM"),
    "goal_intake_validate": ("todai.goal_planner.intake_validate", "_GROQ_STRUCTURED_SYSTEM"),
    "goal_confirm_normalize": ("todai.goal_planner.turn_normalize", "_CONFIRM_SYSTEM"),
    "goal_confirm_edits": ("todai.goal_planner.turn_normalize", "_CONFIRM_EDITS_SYSTEM"),
    "goal_delete_normalize": ("todai.goal_planner.task_manage_query", "_DELETE_GROQ_SYSTEM"),
    "goal_tasks_summary": ("todai.goal_planner.task_summary_reply", "_TASK_SUMMARY_SYSTEM"),
    "goal_chat": ("todai.goal_planner.chat", "_CHAT_SYSTEM"),
    "goal_manage": ("todai.goal_planner.manage", "_MANAGE_SYSTEM"),
    "goal_task_gen": ("todai.goal_planner.task_generator", "_DAY_SYSTEM"),
}

_PROMPT_META: dict[str, dict[str, Any]] = {
    "goal_router": {
        "title": "Goal Router",
        "purpose": "Classify user message → goal route (Groq-first, rules fallback).",
        "intake": "CURRENT_USER_MESSAGE, GOAL_CONTEXT (phase, answers, ui_mode), ROUTING_CONTEXT",
        "routes": ["*"],
        "handler": "route_goal_turn_llm",
        "file": "todai/goal_planner/routing/router.py",
    },
    "goal_intake_init": {
        "title": "Intake Init",
        "purpose": "Turn achievement text into first tailored setup question.",
        "intake": "achievement, title, description",
        "routes": ["goal_interrogate"],
        "handler": "handle_ai_intake_turn (init)",
        "file": "todai/goal_planner/ai_intake.py",
    },
    "goal_intake_finalize": {
        "title": "Intake Finalize",
        "purpose": "Map completed Q&A → objective, tasks/day, skip days.",
        "intake": "question/answer transcript",
        "routes": ["goal_interrogate"],
        "handler": "handle_ai_intake_turn (finalize)",
        "file": "todai/goal_planner/ai_intake.py",
    },
    "goal_intake_validate": {
        "title": "Intake Validate",
        "purpose": "Normalize one intake answer (objective / tasks_per_day / skip_days).",
        "intake": "field kind + raw user text",
        "routes": ["goal_interrogate"],
        "handler": "groq_structured_intake",
        "file": "todai/goal_planner/intake_validate.py",
    },
    "goal_confirm_normalize": {
        "title": "Confirm Yes/No",
        "purpose": "Detect standalone yes/no at pre-build review.",
        "intake": "user message at confirm phase",
        "routes": ["goal_confirm"],
        "handler": "normalize_confirmation",
        "file": "todai/goal_planner/turn_normalize.py",
    },
    "goal_confirm_edits": {
        "title": "Confirm Edits",
        "purpose": "Parse inline setting changes; merge skip_days on add/also/as well.",
        "intake": "message + current_settings (objective, tasks/day, skip_days)",
        "routes": ["goal_confirm"],
        "handler": "_groq_confirm_edits",
        "file": "todai/goal_planner/turn_normalize.py",
    },
    "goal_delete_normalize": {
        "title": "Delete Normalize",
        "purpose": "Parse delete/manage intents (goal, plan, day, task).",
        "intake": "user message + plan context",
        "routes": ["goal_manage"],
        "handler": "groq_delete_normalize",
        "file": "todai/goal_planner/task_manage_query.py",
    },
    "goal_tasks_summary": {
        "title": "Tasks Summary",
        "purpose": "Natural-language reply for structured task listing queries.",
        "intake": "query + task rows",
        "routes": ["goal_tasks_summary"],
        "handler": "compose_task_summary_reply",
        "file": "todai/goal_planner/task_summary_reply.py",
    },
    "goal_chat": {
        "title": "Goal Coach Chat",
        "purpose": "Coaching/advice on active plans (not task tables).",
        "intake": "message + plan summary + history",
        "routes": ["goal_chat"],
        "handler": "_groq_chat_reply",
        "file": "todai/goal_planner/chat.py",
    },
    "goal_manage": {
        "title": "Goal Manage Reply",
        "purpose": "Human-readable manage/list/delete replies.",
        "intake": "manage action + tool results",
        "routes": ["goal_manage"],
        "handler": "_groq_manage_reply",
        "file": "todai/goal_planner/manage.py",
    },
    "goal_task_gen": {
        "title": "Task Generator",
        "purpose": "Generate one day of tasks (exact count, static pad after Groq).",
        "intake": "day spec, objective, difficulty, skip context",
        "routes": ["goal_create"],
        "handler": "generate_day_tasks",
        "file": "todai/goal_planner/task_generator.py",
    },
}

GOAL_ROUTES: list[dict[str, Any]] = [
    {
        "id": "goal_interrogate",
        "label": "Interrogate",
        "description": "AI setup Q&A on New goal tab — objective, tasks/day, skip days.",
        "phases": ["interrogate"],
        "ui_mode": "new_goal",
        "handler": "handle_ai_intake_turn",
        "prompts": ["goal_router", "goal_intake_init", "goal_intake_finalize", "goal_intake_validate"],
        "pattern": ["User message", "goal_router", "goal_ai_intake", "goal_intake_validate (per answer)"],
    },
    {
        "id": "goal_confirm",
        "label": "Confirm",
        "description": "Review summary; yes/no; inline setting edits before build.",
        "phases": ["confirm"],
        "ui_mode": "new_goal",
        "handler": "handle_ai_confirm",
        "prompts": ["goal_router", "goal_confirm_normalize", "goal_confirm_edits"],
        "pattern": ["User message", "goal_router", "goal_confirm_edits or normalize", "preview → yes"],
    },
    {
        "id": "goal_create",
        "label": "Create",
        "description": "Build 7-day tasks when answers complete and user confirms.",
        "phases": ["ready", "creating", "active"],
        "ui_mode": "new_goal",
        "handler": "_handle_create",
        "prompts": ["goal_router", "goal_task_gen"],
        "pattern": ["User yes", "goal_router", "prefetch calendar", "goal_task_gen × days", "DB write"],
    },
    {
        "id": "goal_manage",
        "label": "Manage",
        "description": "List/delete/edit goals and tasks.",
        "phases": ["active"],
        "ui_mode": "my_goals",
        "handler": "handle_goal_manage",
        "prompts": ["goal_router", "goal_delete_normalize", "goal_manage"],
        "pattern": ["User message", "goal_router", "goal tools", "goal_manage reply"],
    },
    {
        "id": "goal_schedule_read",
        "label": "Schedule Read",
        "description": "Calendar + free-time read for plan window.",
        "phases": ["active"],
        "ui_mode": "my_goals",
        "handler": "_handle_schedule_read",
        "prompts": ["goal_router"],
        "pattern": ["User message", "goal_router", "prefetch get_schedule_range / get_free_time"],
    },
    {
        "id": "goal_tasks_summary",
        "label": "Tasks Summary",
        "description": "Structured task listing (by day, progress).",
        "phases": ["active"],
        "ui_mode": "my_goals",
        "handler": "_handle_tasks_summary",
        "prompts": ["goal_router", "goal_tasks_summary"],
        "pattern": ["User message", "goal_router", "static task query", "goal_tasks_summary"],
    },
    {
        "id": "goal_chat",
        "label": "Coach Chat",
        "description": "Coaching and advice — not a task table.",
        "phases": ["active", "interrogate", "confirm"],
        "ui_mode": "my_goals",
        "handler": "handle_goal_chat",
        "prompts": ["goal_router", "goal_chat"],
        "pattern": ["User message", "goal_router", "goal_chat"],
    },
]

ARCHITECTURE_PATTERN = {
    "title": "Groq-first goal planner",
    "steps": [
        "User speaks naturally (intake, confirm, manage, coach).",
        "goal_router (Groq) picks route; rules + phase guards override when needed.",
        "Route handler calls specialist Groq prompts (intake, confirm, task gen, etc.).",
        "Static code verifies types/ranges and executes DB/calendar writes.",
        "tool_trace + groq_trace returned on every /api/goals/plan/message turn.",
    ],
    "api_entry": "POST /api/goals/plan/message",
    "debug_ui": "/goal-debug",
}


def get_prompt_default_text(prompt_id: str) -> str:
    src = _PROMPT_SOURCES.get(prompt_id)
    if not src:
        raise KeyError(f"Unknown prompt id: {prompt_id}")
    mod = importlib.import_module(src[0])
    value = getattr(mod, src[1], None)
    if not isinstance(value, str):
        raise TypeError(f"{prompt_id} default is not a string")
    return value


def list_prompt_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for prompt_id, meta in _PROMPT_META.items():
        mod_path, const = _PROMPT_SOURCES[prompt_id]
        entries.append(
            {
                "id": prompt_id,
                "phase": prompt_id,
                "title": meta["title"],
                "purpose": meta["purpose"],
                "intake": meta["intake"],
                "routes": meta["routes"],
                "handler": meta["handler"],
                "file": meta["file"],
                "constant": const,
                "module": mod_path,
            }
        )
    return entries


def get_prompt_entry(prompt_id: str) -> dict[str, Any]:
    for entry in list_prompt_entries():
        if entry["id"] == prompt_id:
            return entry
    raise KeyError(prompt_id)


def get_goal_catalog() -> dict[str, Any]:
    return {
        "architecture": ARCHITECTURE_PATTERN,
        "routes": GOAL_ROUTES,
        "prompts": list_prompt_entries(),
        "api": {
            "start": "POST /api/goals/plan/start",
            "message": "POST /api/goals/plan/message",
            "list": "GET /api/goals/plan/plans",
            "state": "GET /api/goals/plan/{plan_id}",
            "debug_catalog": "GET /api/goals/debug/catalog",
            "debug_history": "GET /api/goals/debug/plans/{plan_id}/history",
        },
    }
