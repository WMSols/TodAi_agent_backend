"""
calendar.py — calendar blocks, read tools, and direct writes

Sections:
  1. ISO datetime parsing + merge add/update/remove into block lists
  2. CalendarService — load events / free-time for date ranges
  3. Read-tool registry + validation (router may only request reads)
  4. execute_read_tools — run reads against UserStore
  5. apply_operations_direct — write specialist operations to month JSON files
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from todai.database.storage import UserStore

# ── 1. Scheduling primitives ──────────────────────────────────────────────


def parse_iso_dt(s: str) -> datetime:
    s = s.strip()
    if not s:
        raise ValueError("empty datetime string")
    s = s.replace("Z", "+00:00")
    if "T" not in s:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def merge_operations_into_blocks_lenient(
    current: list[dict],
    operations: list[dict],
) -> list[dict]:
    by_id: dict[str, dict] = {}
    for b in current:
        bid = b.get("id")
        if bid is None:
            bid = f"blk_{uuid4().hex[:8]}"
            b = {**b, "id": bid}
        by_id[str(bid)] = dict(b)
    for op in operations:
        kind = op.get("op")
        if kind == "remove":
            by_id.pop(str(op.get("id")), None)
        elif kind == "update" and str(op.get("id")) in by_id:
            by_id[str(op["id"])]["start"] = op["start"]
            by_id[str(op["id"])]["end"] = op["end"]
            if op.get("title"):
                by_id[str(op["id"])]["title"] = op["title"]
        elif kind == "add":
            aid = op.get("id") or f"blk_{uuid4().hex[:8]}"
            by_id[str(aid)] = {
                "id": str(aid),
                "title": op.get("title", "Block"),
                "start": op["start"],
                "end": op["end"],
                "kind": op.get("kind", "focus"),
            }
    merged = list(by_id.values())
    merged.sort(key=lambda b: parse_iso_dt(b["start"]))
    return merged


# ── 2. Calendar service ───────────────────────────────────────────────────


def _months_spanned(d0: date, d1: date) -> list[str]:
    out: list[str] = []
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def _block_in_range(block: dict, a: date, b: date) -> bool:
    return a <= parse_iso_dt(block["start"]).date() <= b


class CalendarService:
    """All schedule reads for one user go through here (not raw JSON paths)."""

    def __init__(self, store: UserStore):
        self._store = store

    def get_events(self, start: date, end: date) -> list[dict[str, Any]]:
        a, b = start, end if end >= start else start
        blocks: list[dict[str, Any]] = []
        for ym in _months_spanned(a, b):
            doc = self._store.read_calendar_month(ym)
            for blk in doc.get("blocks", []):
                if _block_in_range(blk, a, b):
                    blocks.append({**blk, "_month": ym})
        return sorted(blocks, key=lambda b: parse_iso_dt(str(b["start"])))

    def free_time_days(self, start: date, end: date) -> dict[str, Any]:
        a, b = start, end
        range_start = datetime.combine(a, time.min)
        range_end_excl = datetime.combine(b + timedelta(days=1), time.min)
        blocks: list[dict[str, Any]] = []
        for ym in _months_spanned(a, b):
            for blk in self._store.read_calendar_month(ym).get("blocks", []):
                bs, be = parse_iso_dt(blk["start"]), parse_iso_dt(blk["end"])
                if bs < range_end_excl and be > range_start:
                    blocks.append(blk)

        days: list[dict[str, Any]] = []
        d = a
        while d <= b:
            day_start = datetime.combine(d, time.min)
            day_end_excl = day_start + timedelta(days=1)
            busy: list[tuple[datetime, datetime]] = []
            for blk in blocks:
                bs, be = parse_iso_dt(blk["start"]), parse_iso_dt(blk["end"])
                if bs < day_end_excl and be > day_start:
                    s, e = max(bs, day_start), min(be, day_end_excl)
                    if e > s:
                        busy.append((s, e))
            busy.sort(key=lambda x: x[0])
            merged: list[tuple[datetime, datetime]] = []
            for s, e in busy:
                if merged and s <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                else:
                    merged.append((s, e))
            gaps: list[tuple[datetime, datetime]] = []
            cur = day_start
            for s, e in merged:
                if s > cur:
                    gaps.append((cur, s))
                cur = max(cur, e)
            if cur < day_end_excl:
                gaps.append((cur, day_end_excl))
            days.append(
                {
                    "date": d.isoformat(),
                    "free_gaps": [
                        {"start": gs.isoformat(timespec="minutes"), "end": ge.isoformat(timespec="minutes")}
                        for gs, ge in gaps
                    ],
                    "busy_intervals": len(merged),
                }
            )
            d += timedelta(days=1)
        return {"from": start.isoformat(), "to": end.isoformat(), "days": days, "blocks_overlapping_range": len(blocks)}


# ── 3. Read-tool registry ─────────────────────────────────────────────────

MAX_RANGE_DAYS = 14


class ToolSideEffect(str, Enum):
    READ = "read"


class GetScheduleRangeArgs(BaseModel):
    from_: str = Field(..., alias="from")
    to: str

    @field_validator("from_", "to")
    @classmethod
    def iso_date(cls, v: str) -> str:
        parts = v.strip()[:10]
        if len(parts) != 10 or parts[4] != "-" or parts[7] != "-":
            raise ValueError("expected YYYY-MM-DD")
        return parts


class GetActiveGoalsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeProgressArgs(BaseModel):
    from_: str | None = Field(None, alias="from")
    to: str | None = None


TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "get_schedule_range": {"side_effect": ToolSideEffect.READ, "args_model": GetScheduleRangeArgs},
    "get_free_time": {"side_effect": ToolSideEffect.READ, "args_model": GetScheduleRangeArgs},
    "get_active_goals": {"side_effect": ToolSideEffect.READ, "args_model": GetActiveGoalsArgs},
    "analyze_progress": {"side_effect": ToolSideEffect.READ, "args_model": AnalyzeProgressArgs},
}

_READ_TOOLS = {n for n, m in TOOL_REGISTRY.items() if m.get("side_effect") == ToolSideEffect.READ}


def _normalize_range_args(args: dict[str, Any]) -> dict[str, Any]:
    """Map common LLM mistakes (start_date/end_date) to from/to."""
    a = dict(args or {})
    if "from" not in a and a.get("start_date"):
        a["from"] = a["start_date"]
    if "to" not in a and a.get("end_date"):
        a["to"] = a["end_date"]
    if "from" not in a and a.get("from_date"):
        a["from"] = a["from_date"]
    if "to" not in a and a.get("to_date"):
        a["to"] = a["to_date"]
    return a


def validate_tool_plan(
    tool_plan: list[dict[str, Any]] | None,
    *,
    server_today: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not tool_plan:
        return [], []
    errors: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for i, call in enumerate(tool_plan):
        if not isinstance(call, dict):
            errors.append({"code": "INVALID_TOOL", "index": i})
            continue
        call = dict(call)
        args = dict(call.get("arguments") or {})
        for key in ("from", "to"):
            if key in call and key not in args:
                args[key] = call.pop(key)
        call["arguments"] = args
        tool = str(call.get("tool") or "").strip()
        if tool not in _READ_TOOLS:
            errors.append({"code": "TOOL_NOT_ALLOWED", "tool": tool})
            continue
        args = _normalize_range_args(call.get("arguments") or {})
        model = TOOL_REGISTRY[tool]["args_model"]
        try:
            if tool in ("get_schedule_range", "get_free_time"):
                validated = model.model_validate(args)
                a = datetime.strptime(validated.from_[:10], "%Y-%m-%d").date()
                b = datetime.strptime(validated.to[:10], "%Y-%m-%d").date()
                if b < a:
                    errors.append({"code": "INVALID_RANGE", "tool": tool})
                    continue
                if (b - a).days > MAX_RANGE_DAYS:
                    b = a + timedelta(days=MAX_RANGE_DAYS)
                out.append({"tool": tool, "arguments": {"from": validated.from_[:10], "to": b.isoformat()}})
            else:
                validated = model.model_validate(args)
                out.append({"tool": tool, "arguments": validated.model_dump(by_alias=True)})
        except Exception as e:
            errors.append({"code": "ARGS_VALIDATION", "tool": tool, "detail": str(e)})
    return _dedupe_calls(out), errors


def _dedupe_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for call in calls:
        key = call.get("tool", "") + ":" + json.dumps(call.get("arguments") or {}, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(call)
    return out


# ── 4. Execute read tools ─────────────────────────────────────────────────


def _daterange_bounds(from_s: str, to_s: str) -> tuple[date, date]:
    return (
        datetime.strptime(from_s[:10], "%Y-%m-%d").date(),
        datetime.strptime(to_s[:10], "%Y-%m-%d").date(),
    )


def execute_read_tools(
    store: UserStore,
    tool_calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    svc = CalendarService(store)

    for idx, call in enumerate(tool_calls):
        tool = call["tool"]
        args = call.get("arguments") or {}
        try:
            if tool == "get_active_goals":
                goals = [g for g in store.read_profile().get("goals", []) if g.get("status") == "active"]
                results.append({"tool": tool, "ok": True, "data": {"goals": goals}})
            elif tool == "get_schedule_range":
                p = GetScheduleRangeArgs.model_validate({**args, "from": args.get("from")})
                a, b = _daterange_bounds(p.from_, p.to)
                blocks = svc.get_events(a, b)
                results.append({"tool": tool, "ok": True, "data": {"from": p.from_, "to": p.to, "blocks": blocks}})
            elif tool == "get_free_time":
                p = GetScheduleRangeArgs.model_validate({**args, "from": args.get("from")})
                a, b = _daterange_bounds(p.from_, p.to)
                results.append({"tool": tool, "ok": True, "data": svc.free_time_days(a, b)})
            elif tool == "analyze_progress":
                goals = store.read_profile().get("goals", [])
                results.append(
                    {
                        "tool": tool,
                        "ok": True,
                        "data": {
                            "active_goals": len([g for g in goals if g.get("status") == "active"]),
                            "note": "sandbox stub",
                        },
                    }
                )
            else:
                errors.append({"code": "UNSUPPORTED_READ", "tool_index": idx, "detail": tool})
        except Exception as e:
            errors.append({"code": "TOOL_EXEC_ERROR", "tool_index": idx, "detail": str(e)})
    return results, errors


def run_prefetch(store: UserStore, calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not calls:
        return [], []
    return execute_read_tools(store, calls)


# ── 5. Direct apply (specialist operations → JSON files) ──────────────────


def _find_month(store: UserStore, block_id: str) -> str | None:
    if not block_id:
        return None
    for p in store.paths.root.glob("calendar_*.json"):
        ym = p.stem.replace("calendar_", "")
        if any(b.get("id") == block_id for b in store.read_calendar_month(ym).get("blocks", [])):
            return ym
    return None


def apply_operations_direct(
    store: UserStore,
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not operations:
        return [], 0

    by_month: dict[str, list[dict[str, Any]]] = {}
    for op in operations:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op", "")).lower()
        ym = None
        if kind == "add":
            start = str(op.get("start", ""))
            ym = start[:7] if len(start) >= 7 else None
        elif kind in ("update", "remove"):
            ym = _find_month(store, str(op.get("id", "")))
        if ym:
            by_month.setdefault(ym, []).append(op)

    if not by_month:
        return [{"code": "APPLY_FAILED", "detail": "could not resolve months"}], 0

    errors: list[dict[str, Any]] = []
    written = 0
    for ym, ops in by_month.items():
        doc = store.read_calendar_month(ym)
        cur = doc.get("blocks") or []
        try:
            merged = merge_operations_into_blocks_lenient(cur, ops)
        except ValueError as e:
            errors.append({"code": "APPLY_FAILED", "detail": str(e), "month": ym})
            continue
        store.write_calendar_month(
            ym,
            {"month": ym, "version": int(doc.get("version", 1)) + 1, "blocks": merged},
        )
        written += 1
    return errors, written
