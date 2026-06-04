"""REST calendar events — direct CRUD via Supabase (no agent)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from todai.calendar_api.recurrence import expand_weekly_occurrences
from todai.database import user_store
from todai.database.repositories.supabase.context import SupabaseContext
from todai.database.repositories.supabase.helpers import (
    get_supabase_client,
    local_naive_to_utc,
    parse_ts,
    resolve_db_user_id,
    utc_to_local_naive_str,
)
from todai.database.repositories.supabase.profile import SupabaseProfileRepository
from todai.database.utils.tz import get_timezone


def _parse_local_dt(value: str) -> datetime:
    raw = (value or "").strip().replace("Z", "")
    if "T" not in raw:
        raw = raw + "T00:00:00"
    return datetime.fromisoformat(raw[:19])


def _event_api_row(row: dict[str, Any], tz: str, recurrence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "title": row.get("title") or "Event",
        "description": row.get("description") or "",
        "start": utc_to_local_naive_str(parse_ts(row["start_at"]), tz),
        "end": utc_to_local_naive_str(parse_ts(row["end_at"]), tz),
        "kind": row.get("kind") or "personal",
        "location": row.get("location") or "",
        "all_day": bool(row.get("all_day")),
        "source": row.get("source") or "user",
        "recurrence_id": str(row["recurrence_id"]) if row.get("recurrence_id") else None,
        "recurrence": recurrence,
    }


def _bump_schedule_version(store: Any) -> int:
    chat = store.read_chat()
    v = int(chat.get("schedule_version", 1)) + 1
    chat["schedule_version"] = v
    store.write_chat(chat)
    return v


def list_events(user_id: str, *, date_from: str, date_to: str) -> dict[str, Any]:
    a = date.fromisoformat(date_from[:10])
    b = date.fromisoformat(date_to[:10])
    if b < a:
        raise ValueError("to must be on or after from")
    return _list_events(user_id, a, b)


def _list_events(user_id: str, a: date, b: date) -> dict[str, Any]:
    client = get_supabase_client()
    db_uid = resolve_db_user_id(user_id)
    ctx = SupabaseContext(client=client, api_user_id=user_id, db_user_id=db_uid)
    profile = SupabaseProfileRepository(ctx)
    tz = profile.tz_name()
    z = get_timezone(tz)
    range_start = datetime.combine(a, time.min, tzinfo=z).astimezone(__import__("datetime").timezone.utc)
    range_end = datetime.combine(b, time.max.replace(microsecond=0), tzinfo=z).astimezone(
        __import__("datetime").timezone.utc
    )
    rows_data, has_recurrence = _query_events_in_range(
        client, db_uid, range_start, range_end
    )
    rec_map: dict[str, dict[str, Any]] = {}
    if has_recurrence:
        rec_ids = {str(r["recurrence_id"]) for r in rows_data if r.get("recurrence_id")}
        if rec_ids:
            try:
                rec_rows = (
                    client.table("calendar_recurrence")
                    .select(
                        "id, frequency, weekly_mode, skip_days, repeat_weeks, "
                        "anchor_start_at, anchor_end_at"
                    )
                    .in_("id", list(rec_ids))
                    .execute()
                )
                for rr in rec_rows.data or []:
                    rec_map[str(rr["id"])] = _recurrence_api(rr, tz)
            except Exception:
                pass

    events = [
        _event_api_row(r, tz, rec_map.get(str(r["recurrence_id"])) if r.get("recurrence_id") else None)
        for r in rows_data
    ]
    return {"from": a.isoformat(), "to": b.isoformat(), "timezone": tz, "events": events}


def _goal_task_api_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "title": row.get("title") or "Goal task",
        "description": row.get("description") or "",
        "task_date": str(row.get("task_date", ""))[:10],
        "start_time": row.get("start_time") or "",
        "end_time": row.get("end_time") or "",
        "status": row.get("status") or "pending",
        "plan_id": str(row["plan_id"]) if row.get("plan_id") else None,
        "goal_id": str(row["goal_id"]) if row.get("goal_id") else None,
        "kind": "goal_task",
    }


def list_agenda(user_id: str, *, date_from: str, date_to: str) -> dict[str, Any]:
    """Calendar events plus goal tasks for the My events grid."""
    from todai.goal_planner.session_store import GoalPlanSessionStore

    payload = list_events(user_id, date_from=date_from, date_to=date_to)
    a = date.fromisoformat(date_from[:10])
    b = date.fromisoformat(date_to[:10])
    store = GoalPlanSessionStore(user_id)
    tasks = store.list_goal_tasks_in_range(a, b)
    payload["goal_tasks"] = [_goal_task_api_row(t) for t in tasks]
    return payload


def _query_events_in_range(
    client: Any,
    db_uid: str,
    range_start: datetime,
    range_end: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (rows, has_recurrence_column). Retries without recurrence_id if migration missing."""
    cols_full = (
        "id, title, description, start_at, end_at, kind, location, all_day, source, recurrence_id"
    )
    cols_basic = "id, title, description, start_at, end_at, kind, location, all_day, source"

    def _run(cols: str):
        return (
            client.table("calendar_events")
            .select(cols)
            .eq("user_id", db_uid)
            .is_("deleted_at", "null")
            .eq("status", "confirmed")
            .gte("start_at", range_start.isoformat())
            .lte("start_at", range_end.isoformat())
            .order("start_at")
            .execute()
        )

    try:
        rows = _run(cols_full)
        return list(rows.data or []), True
    except Exception as exc:
        msg = str(exc).lower()
        if "recurrence_id" in msg or "42703" in msg:
            rows = _run(cols_basic)
            return list(rows.data or []), False
        raise


def _recurrence_api(row: dict[str, Any], tz: str) -> dict[str, Any]:
    skip = row.get("skip_days") or []
    return {
        "id": str(row["id"]),
        "frequency": row.get("frequency") or "weekly",
        "weekly_mode": row.get("weekly_mode") or "same_day",
        "skip_days": [int(x) for x in skip],
        "repeat_weeks": int(row.get("repeat_weeks") or 12),
        "anchor_start": utc_to_local_naive_str(parse_ts(row["anchor_start_at"]), tz),
        "anchor_end": utc_to_local_naive_str(parse_ts(row["anchor_end_at"]), tz),
    }


def create_event(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return _create_event(user_id, body)


def _create_event(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    client = get_supabase_client()
    db_uid = resolve_db_user_id(user_id)
    ctx = SupabaseContext(client=client, api_user_id=user_id, db_user_id=db_uid)
    profile = SupabaseProfileRepository(ctx)
    tz = profile.tz_name()

    start = _parse_local_dt(body["start"])
    end = _parse_local_dt(body["end"])
    rec = body.get("recurrence") or {}
    occurrences = [(start, end)]
    recurrence_meta = None
    recurrence_id = None

    if rec.get("enabled"):
        skip = {int(x) for x in (rec.get("skip_days") or [])}
        mode = rec.get("weekly_mode") or "same_day"
        weeks = int(rec.get("repeat_weeks") or 12)
        occurrences = expand_weekly_occurrences(
            start, end, repeat_weeks=weeks, skip_days=skip, weekly_mode=mode
        )
        if not occurrences:
            raise ValueError("recurrence produced no occurrences; adjust skip days or dates")
        rec_ins = (
            client.table("calendar_recurrence")
            .insert(
                {
                    "user_id": db_uid,
                    "frequency": "weekly",
                    "weekly_mode": mode,
                    "skip_days": sorted(skip),
                    "repeat_weeks": weeks,
                    "anchor_start_at": local_naive_to_utc(body["start"], tz).isoformat(),
                    "anchor_end_at": local_naive_to_utc(body["end"], tz).isoformat(),
                }
            )
            .execute()
        )
        recurrence_id = str(rec_ins.data[0]["id"])
        recurrence_meta = _recurrence_api(rec_ins.data[0], tz)

    created: list[dict[str, Any]] = []
    for st, en in occurrences:
        row = {
            "id": str(uuid.uuid4()),
            "user_id": db_uid,
            "title": body.get("title") or "Event",
            "description": body.get("description") or None,
            "start_at": local_naive_to_utc(st.strftime("%Y-%m-%dT%H:%M:%S"), tz).isoformat(),
            "end_at": local_naive_to_utc(en.strftime("%Y-%m-%dT%H:%M:%S"), tz).isoformat(),
            "kind": body.get("kind") or "personal",
            "location": body.get("location") or None,
            "all_day": bool(body.get("all_day")),
            "source": "user",
            "status": "confirmed",
            "recurrence_id": recurrence_id,
            "deleted_at": None,
        }
        ins = client.table("calendar_events").insert(row).execute()
        created.append(_event_api_row(ins.data[0], tz, recurrence_meta))

    with user_store(user_id) as store:
        ver = _bump_schedule_version(store)
    return {
        "ok": True,
        "events": created,
        "recurrence": recurrence_meta,
        "schedule_version": ver,
    }


def update_event(user_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return _update_event(user_id, event_id, body)


def _update_event(user_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
    client = get_supabase_client()
    db_uid = resolve_db_user_id(user_id)
    ctx = SupabaseContext(client=client, api_user_id=user_id, db_user_id=db_uid)
    profile = SupabaseProfileRepository(ctx)
    tz = profile.tz_name()
    patch: dict[str, Any] = {"updated_at": datetime.now(__import__("datetime").timezone.utc).isoformat()}
    if "title" in body:
        patch["title"] = body["title"]
    if "description" in body:
        patch["description"] = body["description"]
    if "start" in body:
        patch["start_at"] = local_naive_to_utc(body["start"], tz).isoformat()
    if "end" in body:
        patch["end_at"] = local_naive_to_utc(body["end"], tz).isoformat()
    if "kind" in body:
        patch["kind"] = body["kind"]
    if "location" in body:
        patch["location"] = body["location"]
    if "all_day" in body:
        patch["all_day"] = body["all_day"]
    upd = (
        client.table("calendar_events")
        .update(patch)
        .eq("id", event_id)
        .eq("user_id", db_uid)
        .is_("deleted_at", "null")
        .execute()
    )
    if not upd.data:
        raise LookupError("event not found")
    row = upd.data[0]
    rec = None
    if row.get("recurrence_id"):
        rr = (
            client.table("calendar_recurrence")
            .select("*")
            .eq("id", row["recurrence_id"])
            .limit(1)
            .execute()
        )
        if rr.data:
            rec = _recurrence_api(rr.data[0], tz)
    with user_store(user_id) as store:
        ver = _bump_schedule_version(store)
    return {"ok": True, "event": _event_api_row(row, tz, rec), "schedule_version": ver}


def delete_event(user_id: str, event_id: str, *, delete_series: bool = False) -> dict[str, Any]:
    return _delete_event(user_id, event_id, delete_series=delete_series)


def _delete_event(user_id: str, event_id: str, *, delete_series: bool) -> dict[str, Any]:
    client = get_supabase_client()
    db_uid = resolve_db_user_id(user_id)
    now = datetime.now(__import__("datetime").timezone.utc).isoformat()
    row = (
        client.table("calendar_events")
        .select("id, recurrence_id")
        .eq("id", event_id)
        .eq("user_id", db_uid)
        .limit(1)
        .execute()
    )
    if not row.data:
        raise LookupError("event not found")
    rid = row.data[0].get("recurrence_id")
    removed = 0
    if delete_series and rid:
        upd = (
            client.table("calendar_events")
            .update({"status": "cancelled", "deleted_at": now})
            .eq("recurrence_id", rid)
            .eq("user_id", db_uid)
            .execute()
        )
        removed = len(upd.data or [])
        client.table("calendar_recurrence").delete().eq("id", rid).execute()
    else:
        client.table("calendar_events").update(
            {"status": "cancelled", "deleted_at": now}
        ).eq("id", event_id).eq("user_id", db_uid).execute()
        removed = 1
    with user_store(user_id) as store:
        ver = _bump_schedule_version(store)
    return {"ok": True, "deleted": removed, "schedule_version": ver}
