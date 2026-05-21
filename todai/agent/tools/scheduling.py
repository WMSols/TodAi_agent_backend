"""Scheduling helpers — overlap detection and block merges."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from todai.agent.tools.calendar import merge_operations_into_blocks_lenient, parse_iso_dt


def merge_operations_into_blocks(
    current: list[dict],
    operations: list[dict],
) -> list[dict]:
    return merge_operations_into_blocks_lenient(current, operations)


def find_overlapping_pairs(blocks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    parsed: list[tuple[str, datetime, datetime]] = []

    for b in blocks:
        bid = str(b.get("id", ""))
        if not bid:
            continue
        try:
            start = parse_iso_dt(str(b["start"]))
            end = parse_iso_dt(str(b["end"]))
        except (KeyError, ValueError):
            continue
        parsed.append((bid, start, end))

    for i, (ida, sa, ea) in enumerate(parsed):
        for j in range(i + 1, len(parsed)):
            idb, sb, eb = parsed[j]
            if sa < eb and sb < ea:
                pairs.append((ida, idb))
    return pairs
