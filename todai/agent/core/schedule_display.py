"""
Schedule presentation for the calendar agent.

  - UI JSON (schedule_display panel)
  - Human-readable lines for chat replies
  - Refresh after writes
  - Grounded preview replies from prefetch (no LLM guesswork on single-day reads)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from todai.agent.planner.prompt_bundles import extract_days_without_schedule_bundle
from todai.agent.routing.preview_range import PreviewRange
from todai.agent.routing.preview_range import PreviewReadKind, classify_preview_read
from todai.agent.tools.calendar import execute_read_tools, parse_iso_dt
from todai.database.storage import UserStore, parse_server_date

__all__ = [
    "build_schedule_display",
    "build_upcoming_schedule_highlights",
    "build_week_schedule_display",
    "build_schedule_agent_message",
    "format_block_line",
    "format_schedule_read_results",
    "pick_schedule_assistant_text",
    "build_grounded_preview_reply",
    "build_period_preview_reply",
    "build_free_days_period_reply",
    "_empty_day_row",
    "_fmt_clock",
]


# --- Time formatting ---


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


# --- UI schedule panel ---


def _empty_day_row(dt: datetime) -> dict[str, Any]:
    return {
        "date": dt.date().isoformat(),
        "weekday": dt.strftime("%A"),
        "day": dt.day,
        "month": dt.strftime("%B"),
        "day_label": f"{dt.strftime('%A')} · {dt.day} {dt.strftime('%B')}",
        "slots": [],
    }


def build_upcoming_schedule_highlights(
    store: UserStore,
    full_index: dict[str, Any] | None,
    *,
    days: int = 7,
) -> dict[str, Any]:
    today = parse_server_date(full_index)
    end = today + timedelta(days=days)
    read_results, _ = execute_read_tools(
        store,
        [{"tool": "get_schedule_range", "arguments": {"from": today.isoformat(), "to": end.isoformat()}}],
    )
    blocks: list[dict[str, Any]] = []
    for r in read_results:
        if r.get("tool") == "get_schedule_range" and r.get("ok"):
            blocks.extend((r.get("data") or {}).get("blocks") or [])

    by_day: dict[str, list[dict[str, str]]] = {}
    for b in sorted(blocks, key=lambda x: str(x.get("start", ""))):
        try:
            key = parse_iso_dt(str(b["start"])).date().isoformat()
        except ValueError:
            continue
        by_day.setdefault(key, []).append(
            {
                "id": str(b.get("id", "")),
                "title": str(b.get("title", "Event")),
                "start": str(b.get("start", "")),
                "end": str(b.get("end", "")),
            }
        )

    day_rows = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        iso = d.isoformat()
        events = by_day.get(iso, [])
        day_rows.append(
            {
                "date": iso,
                "weekday": d.strftime("%A"),
                "event_count": len(events),
                "events": events,
                "free": len(events) == 0,
            }
        )
    return {"server_today": today.isoformat(), "range_to": end.isoformat(), "days": day_rows, "total_events": len(blocks)}


def build_schedule_display(
    tool_results: list[dict[str, Any]],
    *,
    period_from: str | None = None,
    period_to: str | None = None,
    fill_empty_days: bool = True,
    title: str | None = None,
    show_free_banners: bool = False,
) -> dict[str, Any] | None:
    blocks: list[dict[str, Any]] = []
    for r in tool_results:
        if r.get("tool") == "get_schedule_range" and r.get("ok"):
            blocks.extend((r.get("data") or {}).get("blocks") or [])

    if not any(r.get("tool") in ("get_schedule_range", "get_free_time") and r.get("ok") for r in tool_results):
        return None

    by_date: dict[str, dict[str, Any]] = {}
    for block in sorted(blocks, key=lambda b: parse_iso_dt(str(b["start"]))):
        start = parse_iso_dt(str(block["start"]))
        end = parse_iso_dt(str(block["end"]))
        date_key = start.date().isoformat()
        if date_key not in by_date:
            by_date[date_key] = _empty_day_row(start)
        event_title = (block.get("title") or "Event").strip()
        by_date[date_key]["slots"].append(
            {"time": f"{_fmt_clock(start)} – {_fmt_clock(end)}", "title": event_title}
        )

    period: dict[str, str] = {}
    for r in tool_results:
        if r.get("tool") == "get_schedule_range" and r.get("ok"):
            data = r.get("data") or {}
            if data.get("from"):
                period["from"] = str(data["from"])[:10]
            if data.get("to"):
                period["to"] = str(data["to"])[:10]
            break

    p_from = (period_from or period.get("from") or "")[:10]
    p_to = (period_to or period.get("to") or "")[:10]

    if fill_empty_days and p_from and p_to:
        try:
            a = date.fromisoformat(p_from)
            b = date.fromisoformat(p_to)
            d = a
            while d <= b:
                key = d.isoformat()
                if key not in by_date:
                    by_date[key] = _empty_day_row(datetime.combine(d, time.min))
                d += timedelta(days=1)
            period["from"] = p_from
            period["to"] = p_to
        except ValueError:
            pass

    free_days: list[dict[str, Any]] = []
    if show_free_banners:
        free_days = [
            {
                "date": k,
                "weekday": by_date[k]["weekday"],
                "day": by_date[k]["day"],
                "month": by_date[k]["month"],
                "label": "Free",
            }
            for k in sorted(by_date.keys())
            if not by_date[k]["slots"]
        ]

    return {
        "schema": "todai.schedule.v1",
        "type": "schedule",
        "title": title or "Your schedule",
        "empty": len(by_date) == 0,
        "period": period,
        "days": [by_date[k] for k in sorted(by_date.keys())],
        "free_days": free_days,
    }


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


# --- Refresh after calendar writes ---


def build_week_schedule_display(
    store: UserStore,
    full_index: dict[str, Any],
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    from todai.agent.core.goal_overlay import build_schedule_display_with_goals
    from todai.agent.routing.preview_range import agent_window_bounds

    today = parse_server_date(full_index)
    _, period_end = agent_window_bounds(today)
    period_to = period_end.isoformat()
    read_results, _ = execute_read_tools(
        store,
        [{"tool": "get_schedule_range", "arguments": {"from": today.isoformat(), "to": period_to}}],
    )
    uid = user_id or str(full_index.get("user_id") or "")
    if not uid:
        return build_schedule_display(
            read_results,
            period_from=today.isoformat(),
            period_to=period_to,
            fill_empty_days=True,
        )
    return build_schedule_display_with_goals(
        read_results,
        user_id=uid,
        period_from=today.isoformat(),
        period_to=period_to,
        fill_empty_days=True,
    )


# --- Grounded preview replies ---


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


def build_period_preview_reply(
    read_results: list[dict[str, Any]],
    preview: PreviewRange,
) -> str | None:
    """Deterministic multi-day schedule summary when the specialist LLM is unavailable."""
    if not any(r.get("tool") == "get_schedule_range" and r.get("ok") for r in read_results):
        return None
    body = format_schedule_read_results(read_results)
    label = preview.label or f"{preview.date_from} – {preview.date_to}"
    if body:
        return f"Here's your schedule for **{label}**:\n\n{body}"
    return f"Nothing on your calendar for **{label}**."


def _grounded_day_schedule_line(
    read_results: list[dict[str, Any]],
    *,
    day_iso: str,
    label: str,
) -> str:
    blocks = _blocks_on_day(read_results, day_iso)
    if not blocks:
        return f"Nothing scheduled on {label}."
    if len(blocks) == 1:
        b = blocks[0]
        blk_title = (b.get("title") or "Event").strip()
        return f"On {label}: {blk_title} ({format_block_line(b)})."
    titles = "; ".join((b.get("title") or "Event") for b in blocks[:4])
    more = f" (+{len(blocks) - 4} more)" if len(blocks) > 4 else ""
    return f"On {label}: {titles}{more}."


def build_grounded_preview_reply(
    *,
    message: str,
    read_results: list[dict[str, Any]],
    preview: PreviewRange,
) -> str | None:
    """
    Deterministic reply for single-day or discrete multi-day schedule / free-day questions.
    Returns None when the specialist should answer (wide scope, free-time slots, etc.).
    """
    kind = classify_preview_read(message)

    if preview.scope_mode == "discrete_days" and preview.target_days:
        if kind != PreviewReadKind.SCHEDULE:
            return None
        from datetime import date as date_cls

        lines: list[str] = []
        for day_iso in preview.target_days:
            try:
                label = date_cls.fromisoformat(day_iso[:10]).strftime("%A, %d %B %Y")
            except ValueError:
                label = day_iso
            lines.append(_grounded_day_schedule_line(read_results, day_iso=day_iso, label=label))
        return " ".join(lines)

    if preview.granularity != "day" or preview.date_from != preview.date_to:
        return None

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

    return _grounded_day_schedule_line(read_results, day_iso=day_iso, label=label)
