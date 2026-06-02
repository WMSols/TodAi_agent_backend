"""
preview_read_kind.py — distinguish schedule view vs free days vs free time slots.
"""

from __future__ import annotations

import re
from enum import Enum


class PreviewReadKind(str, Enum):
    SCHEDULE = "schedule"
    FREE_DAYS = "free_days"
    FREE_TIME = "free_time"


_FREE_DAYS = re.compile(
    r"\bfree\s+days?\b"
    r"|\bdays?\s+without\s+(?:a\s+)?schedule\b"
    r"|\bwithout\s+(?:a\s+)?schedule\b"
    r"|\bno\s+schedule\b"
    r"|\bempty\s+days?\b"
    r"|\bdays?\s+(?:with\s+)?no\s+(?:events?|plans?)\b"
    r"|\bwhich\s+days?\s+(?:are\s+)?free\b"
    r"|\bany\s+free\s+days?\b",
    re.I,
)

_FREE_TIME = re.compile(
    r"\bfree\s+time\b"
    r"|\bfree\s+slots?\b"
    r"|\bavailable\s+(?:time|slots?)\b"
    r"|\btime\s+slots?\b"
    r"|\bwhen\s+(?:am\s+)?i\s+free\b"
    r"|\bgaps?\s+(?:in\s+)?(?:my\s+)?(?:day|schedule)\b"
    r"|\bopen\s+(?:time|slots?)\b",
    re.I,
)


def classify_preview_read(message: str) -> PreviewReadKind:
    """Free-day questions win over free-time when both could match."""
    m = (message or "").strip()
    if not m:
        return PreviewReadKind.SCHEDULE
    if _FREE_DAYS.search(m):
        return PreviewReadKind.FREE_DAYS
    if _FREE_TIME.search(m):
        return PreviewReadKind.FREE_TIME
    return PreviewReadKind.SCHEDULE
