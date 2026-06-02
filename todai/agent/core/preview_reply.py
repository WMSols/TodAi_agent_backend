"""
preview_reply.py — grounded one-day schedule answers from prefetch (no LLM guesswork).
"""

from __future__ import annotations

from typing import Any

from todai.agent.core.schedule_format import format_block_line
from todai.agent.routing.preview_range import PreviewRange
from todai.agent.routing.preview_read_kind import PreviewReadKind, classify_preview_read
from todai.agent.planner.prompt_bundles import extract_days_without_schedule_bundle


def _blocks_on_day(read_results: list[dict[str, Any]], day_iso: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for r in read_results:
        if r.get("tool") != "get_schedule_range" or not r.get("ok"):
            continue
        for blk in (r.get("data") or {}).get("blocks") or []:
            if str(blk.get("start", ""))[:10] == day_iso:
                blocks.append(blk)
    blocks.sort(key=lambda b: str(b.get("start", "")))
    return blocks


def build_free_days_period_reply(
    message: str,
    read_results: list[dict[str, Any]],
) -> str | None:
    """List whole empty days for free-days questions over a multi-day scope."""
    if classify_preview_read(message) != PreviewReadKind.FREE_DAYS:
        return None
    bundle = extract_days_without_schedule_bundle(read_results)
    if bundle is None:
        return None
    days = bundle.get("days") or []
    if not days:
        return "You have no completely free days in this period — every day has at least one event."
    names = ", ".join(d.get("label", d.get("date", "")) for d in days[:8])
    extra = f" (and {len(days) - 8} more)" if len(days) > 8 else ""
    return f"Your free days (no events at all): {names}{extra}."


def build_grounded_preview_reply(
    *,
    message: str,
    read_results: list[dict[str, Any]],
    preview: PreviewRange,
) -> str | None:
    """
    Deterministic reply for single-day schedule / free-day questions.
    Returns None when the specialist should answer (wide scope, free-time slots, etc.).
    """
    if preview.granularity != "day" or preview.date_from != preview.date_to:
        return None

    kind = classify_preview_read(message)
    day_iso = preview.date_from
    label = preview.label or day_iso

    if kind == PreviewReadKind.FREE_DAYS:
        bundle = extract_days_without_schedule_bundle(read_results)
        days = (bundle or {}).get("days") or []
        empty_dates = {(d.get("date") or "")[:10] for d in days}
        if day_iso in empty_dates:
            return f"Yes — {label} has no events scheduled."
        return f"No — {label} has events; see the calendar below."

    if kind != PreviewReadKind.SCHEDULE:
        return None

    blocks = _blocks_on_day(read_results, day_iso)
    if not blocks:
        return f"Nothing scheduled on {label}."
    if len(blocks) == 1:
        b = blocks[0]
        title = (b.get("title") or "Event").strip()
        return f"On {label}: {title} ({format_block_line(b)})."
    titles = "; ".join((b.get("title") or "Event") for b in blocks[:4])
    more = f" (+{len(blocks) - 4} more)" if len(blocks) > 4 else ""
    return f"On {label}: {titles}{more}."
