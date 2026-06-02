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


def intervals_overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def find_conflicts_for_interval(
    start: datetime,
    end: datetime,
    existing_blocks: list[dict[str, Any]],
    *,
    exclude_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return existing blocks that overlap [start, end), excluding exclude_id."""
    conflicts: list[dict[str, Any]] = []
    for b in existing_blocks:
        bid = str(b.get("id") or "")
        if exclude_id and bid == exclude_id:
            continue
        try:
            bs = parse_iso_dt(str(b["start"]))
            be = parse_iso_dt(str(b["end"]))
        except (KeyError, ValueError):
            continue
        if intervals_overlap(start, end, bs, be):
            conflicts.append(
                {
                    "id": bid,
                    "title": b.get("title") or "Event",
                    "start": b.get("start"),
                    "end": b.get("end"),
                }
            )
    return conflicts


def find_conflicts_for_operation(
    op: dict[str, Any],
    existing_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kind = str(op.get("op") or "").lower()
    if kind not in ("add", "update"):
        return []
    try:
        start = parse_iso_dt(str(op["start"]))
        end = parse_iso_dt(str(op["end"]))
    except (KeyError, ValueError):
        return []
    exclude = str(op.get("id") or "").strip() if kind == "update" else None
    return find_conflicts_for_interval(start, end, existing_blocks, exclude_id=exclude or None)


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
            if intervals_overlap(sa, ea, sb, eb):
                pairs.append((ida, idb))
    return pairs
