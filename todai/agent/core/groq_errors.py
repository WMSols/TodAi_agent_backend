"""Detect Groq failure / rate-limit replies so intents can use local fallbacks."""

from __future__ import annotations

import re
from typing import Any

_GROQ_FAILURE = re.compile(
    r"\b(?:groq\s+rate\s+limit|rate\s+limit\s*\(429\)|groq\s+http|groq\s+network|"
    r"please\s+wait\s+about\s+\d+\s+seconds)\b",
    re.I,
)


def is_groq_failure_reply(text: str) -> bool:
    return bool(_GROQ_FAILURE.search((text or "").strip()))


def specialist_groq_failed(spec_dbg: dict[str, Any] | None, reply: str) -> bool:
    if isinstance(spec_dbg, dict):
        if spec_dbg.get("rate_limited"):
            return True
        if spec_dbg.get("ok") is False:
            return True
    return is_groq_failure_reply(reply)
