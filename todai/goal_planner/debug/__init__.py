"""Goal planner debug utilities (runtime prompt overrides, turn traces, catalog)."""

from todai.goal_planner.debug.catalog import get_goal_catalog, get_prompt_entry, list_prompt_entries
from todai.goal_planner.debug.prompt_overrides import (
    apply_system_override,
    clear_all_overrides,
    clear_override,
    get_effective_prompt,
    list_overrides,
    set_override,
)
from todai.goal_planner.debug.turn_trace import (
    begin_goal_turn_trace,
    clear_goal_turn_trace,
    get_turn_groq_calls,
    record_groq_call,
)

__all__ = [
    "apply_system_override",
    "begin_goal_turn_trace",
    "clear_all_overrides",
    "clear_goal_turn_trace",
    "clear_override",
    "get_effective_prompt",
    "get_goal_catalog",
    "get_prompt_entry",
    "get_turn_groq_calls",
    "list_overrides",
    "list_prompt_entries",
    "record_groq_call",
    "set_override",
]
