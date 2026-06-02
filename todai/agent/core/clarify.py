"""Shared text heuristics (no orchestrator imports — safe from llm.py)."""

from __future__ import annotations

import re

_CLARIFY_MARKERS = re.compile(
    r"\?|"
    r"\bwhich\b|\bwhat time\b|\bwhat day\b|\bwhen would\b|\bdo you want\b|"
    r"\bplease (?:confirm|specify|tell)\b|\bcan you (?:give|provide|clarify)\b|"
    r"\blet me know\b|\bstill need\b|\banything else\b",
    re.I,
)

# Soft phrases that must not block calendar apply when operations are present.
_SOFT_CONFIRM = re.compile(r"\bplease confirm\b", re.I)


def reply_is_clarifying(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    return bool(_CLARIFY_MARKERS.search(text))


def reply_blocks_apply(route: str, reply: str) -> bool:
    """True when specialist reply should not trigger apply (real questions only)."""
    if route not in ("schedule_write", "schedule_delete"):
        return False
    text = (reply or "").strip()
    if not text:
        return False
    if "?" in text:
        return True
    stripped = _SOFT_CONFIRM.sub("", text).strip()
    if not stripped:
        return False
    return bool(
        re.search(
            r"\b(?:which|what time|what day|when would|do you want|still need|let me know|"
            r"please (?:specify|tell)|can you (?:give|provide|clarify)|anything else)\b",
            stripped,
            re.I,
        )
    )
