"""In-memory runtime prompt overrides for goal Groq phases (not persisted to disk)."""

from __future__ import annotations

import copy
from threading import Lock
from typing import Any

from todai.goal_planner.debug.catalog import get_prompt_default_text, list_prompt_entries

_lock = Lock()
_overrides: dict[str, str] = {}


def set_override(prompt_id: str, content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Prompt content cannot be empty")
    known = {e["id"] for e in list_prompt_entries()}
    if prompt_id not in known:
        raise KeyError(f"Unknown prompt id: {prompt_id}")
    with _lock:
        _overrides[prompt_id] = text
    return get_effective_prompt(prompt_id)


def clear_override(prompt_id: str) -> bool:
    with _lock:
        return _overrides.pop(prompt_id, None) is not None


def clear_all_overrides() -> int:
    with _lock:
        count = len(_overrides)
        _overrides.clear()
        return count


def list_overrides() -> dict[str, str]:
    with _lock:
        return dict(_overrides)


def get_effective_prompt(prompt_id: str) -> dict[str, Any]:
    default = get_prompt_default_text(prompt_id)
    with _lock:
        override = _overrides.get(prompt_id)
    return {
        "id": prompt_id,
        "default": default,
        "override": override,
        "effective": override if override is not None else default,
        "is_overridden": override is not None,
    }


def apply_system_override(
    phase: str,
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, str]], bool]:
    """Replace the first system message when a runtime override exists for this phase."""
    with _lock:
        override = _overrides.get(phase)
    if not override:
        return messages, False

    out = copy.deepcopy(messages)
    for i, msg in enumerate(out):
        if msg.get("role") == "system":
            out[i] = {**msg, "content": override}
            return out, True
    out.insert(0, {"role": "system", "content": override})
    return out, True
