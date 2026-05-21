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


def reply_is_clarifying(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    return bool(_CLARIFY_MARKERS.search(text))
