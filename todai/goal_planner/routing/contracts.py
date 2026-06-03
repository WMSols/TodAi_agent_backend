"""Goal planner router types (aligned with calendar AgentRoute / RouterOutput)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

GoalRoute = Literal[
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_manage",
    "goal_schedule_read",
    "goal_chat",
]

ManageAction = Literal["none", "list", "delete_plan", "delete_goal", "delete_all", "edit"]

_VALID_ROUTES = {
    "goal_interrogate",
    "goal_confirm",
    "goal_create",
    "goal_manage",
    "goal_schedule_read",
    "goal_chat",
    # legacy aliases from rules router
    "goal_goals_list",
    "goal_delete",
    "goal_edit",
}

_MANAGE_ACTIONS = {"none", "list", "delete_plan", "delete_goal", "delete_all", "edit"}

_GOAL_TOOLS = frozenset(
    {
        "list_goals",
        "list_goals_with_progress",
        "get_plan_detail",
        "delete_plan",
        "delete_goal",
        "delete_all_goals",
        "get_schedule_range",
        "get_free_time",
    }
)


class GoalRouteEnum(str, Enum):
    INTERROGATE = "goal_interrogate"
    CONFIRM = "goal_confirm"
    CREATE = "goal_create"
    MANAGE = "goal_manage"
    SCHEDULE_READ = "goal_schedule_read"
    CHAT = "goal_chat"


class GoalRouterModel(BaseModel):
    """Parsed Groq router JSON."""

    route: str = "goal_chat"
    manage_action: str = "none"
    tools: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("route", mode="before")
    @classmethod
    def _norm_route(cls, v: Any) -> str:
        s = str(v or "goal_chat").strip().lower()
        if s in ("goal_goals_list", "goal_list"):
            return "goal_manage"
        if s in ("goal_delete",):
            return "goal_manage"
        if s in ("goal_edit",):
            return "goal_manage"
        return s if s in _VALID_ROUTES else "goal_chat"

    @field_validator("manage_action", mode="before")
    @classmethod
    def _norm_action(cls, v: Any) -> str:
        s = str(v or "none").strip().lower()
        if s in ("delete", "remove"):
            return "delete_goal"
        if s in ("delete_goal", "remove_goal"):
            return "delete_goal"
        if s in ("delete_all", "remove_all", "clear_all"):
            return "delete_all"
        if s in ("list", "review", "show", "progress"):
            return "list"
        return s if s in _MANAGE_ACTIONS else "none"

    @field_validator("tools", mode="before")
    @classmethod
    def _norm_tools(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if isinstance(v, str):
            return [{"tool": v.strip()}] if v.strip() else []
        if not isinstance(v, list):
            return []
        out: list[dict[str, Any]] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append({"tool": item.strip()})
            elif isinstance(item, dict) and item.get("tool"):
                out.append({"tool": str(item["tool"]).strip(), "arguments": item.get("arguments") or {}})
        return out


def normalize_router_tools(tools: list[dict[str, Any]] | str | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    if isinstance(tools, str):
        tools = [tools]
    out: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, str) and t.strip():
            name = t.strip()
        elif isinstance(t, dict):
            name = str(t.get("tool", "")).strip()
        else:
            continue
        if name in _GOAL_TOOLS:
            args = t.get("arguments") or {} if isinstance(t, dict) else {}
            out.append({"tool": name, "arguments": args})
    return out


_LEGACY_MANAGE = {
    "goal_goals_list": "list",
    "goal_list": "list",
    "goal_delete": "delete_goal",
    "goal_edit": "edit",
}


def parse_goal_router_output(raw: dict[str, Any]) -> tuple[GoalRouterModel | None, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return None, [{"code": "INVALID_GOAL_ROUTER", "detail": "expected object"}]
    raw_route = str(raw.get("route") or raw.get("intent") or "").strip().lower()
    manage_action = raw.get("manage_action") or raw.get("action") or "none"
    if str(manage_action).strip().lower() == "none" and raw_route in _LEGACY_MANAGE:
        manage_action = _LEGACY_MANAGE[raw_route]
    normalized = {
        "route": raw_route or raw.get("route") or raw.get("intent"),
        "manage_action": manage_action,
        "tools": normalize_router_tools(raw.get("tools") or raw.get("tool_plan")),
    }
    try:
        return GoalRouterModel.model_validate(normalized), []
    except Exception as e:
        return None, [{"code": "INVALID_GOAL_ROUTER", "detail": str(e)}]
