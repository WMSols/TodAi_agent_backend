"""
display.py — schedule highlights + UI calendar panel JSON
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from todai.agent.tools.calendar import execute_read_tools, parse_iso_dt
from todai.database.storage import UserStore, parse_server_date


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


def _fmt_clock(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour12} {suffix}" if dt.minute == 0 else f"{hour12}:{dt.minute:02d} {suffix}"


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
        title = (block.get("title") or "Event").strip()
        by_date[date_key]["slots"].append({"time": f"{_fmt_clock(start)} – {_fmt_clock(end)}", "title": title})

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


def _empty_day_row(dt: datetime) -> dict[str, Any]:
    return {
        "date": dt.date().isoformat(),
        "weekday": dt.strftime("%A"),
        "day": dt.day,
        "month": dt.strftime("%B"),
        "day_label": f"{dt.strftime('%A')} · {dt.day} {dt.strftime('%B')}",
        "slots": [],
    }
