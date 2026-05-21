"""Human-readable schedule text and display helpers for previews."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from todai.agent.core.display import build_schedule_display  # re-export for tests / callers

__all__ = [
    "build_schedule_display",
    "build_schedule_agent_message",
    "format_block_line",
    "format_schedule_read_results",
    "pick_schedule_assistant_text",
]
from todai.agent.tools.calendar import parse_iso_dt


def _fmt_clock(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour12} {suffix}" if dt.minute == 0 else f"{hour12}:{dt.minute:02d} {suffix}"


def format_block_line(block: dict[str, Any]) -> str:
    start = parse_iso_dt(str(block["start"]))
    end = parse_iso_dt(str(block["end"]))
    title = (block.get("title") or "Event").strip()
    head = f"{start.strftime('%A')}, {start.day} {start.strftime('%B')},"
    return f"{head} {_fmt_clock(start)} to {_fmt_clock(end)}, activity: {title}"


def build_schedule_agent_message(display: dict[str, Any] | None) -> str:
    if not display or display.get("empty"):
        return "Your calendar is clear for that period."
    lines = ["Here is your calendar:"]
    for day in display.get("days") or []:
        label = day.get("day_label") or day.get("date", "")
        slots = day.get("slots") or []
        if not slots:
            lines.append(f"{label}: free")
            continue
        for slot in slots:
            lines.append(f"{label}: {slot.get('time', '')} — {slot.get('title', '')}")
    return "\n".join(lines)


def pick_schedule_assistant_text(fallback: str, display: dict[str, Any] | None) -> str:
    if fallback and fallback.strip():
        return fallback.strip()
    return build_schedule_agent_message(display)


def format_schedule_read_results(tool_results: list[dict[str, Any]]) -> str | None:
    blocks: list[dict[str, Any]] = []
    for r in tool_results:
        if r.get("tool") == "get_schedule_range" and r.get("ok"):
            blocks.extend((r.get("data") or {}).get("blocks") or [])
    if not blocks:
        return None
    lines = [format_block_line(b) for b in sorted(blocks, key=lambda x: str(x.get("start", "")))]
    return "\n".join(lines)
