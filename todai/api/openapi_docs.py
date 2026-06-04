"""Per-endpoint OpenAPI descriptions — purpose, inputs, outputs (Flutter-first)."""

from __future__ import annotations

_BEARER = "**Header:** `Authorization: Bearer <firebase_id_token>` (from Firebase Auth). Required unless server dev mode."


def _doc(
    *,
    purpose: str,
    inputs: str,
    outputs: str,
    flutter: str | None = None,
    errors: str | None = None,
) -> str:
    parts = [f"**Purpose:** {purpose}"]
    if flutter:
        parts.append(f"**Flutter:** {flutter}")
    parts.append(f"**Inputs:** {inputs}")
    parts.append(f"**Outputs (200):** {outputs}")
    if errors:
        parts.append(f"**Errors:** {errors}")
    return "\n\n".join(parts)


# --- Auth ---

DOC_HEALTH = _doc(
    purpose="Check that the API server is running and which auth/storage backends are enabled.",
    inputs="None.",
    outputs="`ok` (bool), `planner`, `storage`, `supabase_configured`, `firebase_configured`, `local_auth_configured`, `auth_required`.",
)

DOC_AUTH_CONFIG = _doc(
    purpose="Tell the mobile app whether it must send a Bearer token and which login methods the server supports.",
    inputs="None.",
    outputs="`auth_required`, `auth_dev_allow_default`, `storage`, `providers.firebase`, `providers.local`, `firebase_project_id`.",
    flutter="Call once at app startup. If `auth_required` is true, attach Firebase ID token on every `/api/*` request.",
)

DOC_AUTH_REGISTER = _doc(
    purpose="Create a username/password account for the **web** UI only.",
    inputs="JSON body: `display_name` (string), `email` (optional string), `password` (string).",
    outputs="`access_token` (JWT), `token_type` (`bearer`), `expires_in` (seconds), `user` (`id`, `login_name`, `display_name`, `email`).",
    flutter="Do **not** use this endpoint. Use Firebase Auth on the device instead.",
    errors="400 username taken · 503 auth not configured",
)

DOC_AUTH_LOGIN = _doc(
    purpose="Sign in with username or email + password for the **web** UI only.",
    inputs="JSON body: `username` (login name or email), `password`.",
    outputs="Same as register: `access_token`, `token_type`, `expires_in`, `user`.",
    flutter="Do **not** use this endpoint. Use `FirebaseAuth.instance.currentUser!.getIdToken()`.",
    errors="401 invalid credentials",
)

DOC_AUTH_BOOTSTRAP = _doc(
    purpose="Create the user profile, settings, and seed calendar in the database after first sign-in.",
    inputs=_BEARER,
    outputs="`ok`, `user_id`, `display_name`, `email`, `storage`. Safe to call multiple times (idempotent).",
    flutter="Call **once** after Firebase sign-in, before chat or calendar APIs.",
    errors="401 missing/invalid token",
)

# --- Chat ---

DOC_CHAT = _doc(
    purpose="Send natural-language messages to the calendar AI agent (scheduling, questions, confirmations).",
    inputs=(
        f"{_BEARER}\n\n"
        "JSON body: `message` (string, required). `user_id` is ignored when Bearer token is present."
    ),
    outputs=(
        "`assistant_text` (show to user), `state` (`idle` | `analyzing` | `requesting_data` | `error`), "
        "`schedule_version` (int — bump when calendar changed), `schedule_display` (optional JSON for calendar UI), "
        "`pending_proposal_id` (when user must confirm), `tool_trace`, `api_usage`."
    ),
    flutter="Main chat screen. Refresh Firebase token on 401. Call bootstrap first if you get 404 user profile.",
    errors="401 auth · 404 profile missing (run bootstrap)",
)

# --- Calendar ---

DOC_CAL_LIST_EVENTS = _doc(
    purpose="Load **schedule events only** (no goal tasks) for a date range.",
    inputs=f"{_BEARER}\n\nQuery: `from` (YYYY-MM-DD), `to` (YYYY-MM-DD), both inclusive.",
    outputs="`from`, `to`, `timezone`, `events[]` — each event: `id`, `title`, `description`, `start`, `end`, `kind`, `location`, `all_day`, `source`, `recurrence_id`, `recurrence`.",
    flutter="Use when you only need purple schedule blocks. For the month grid, prefer `GET /api/calendar/agenda`.",
    errors="401 auth · 400 invalid dates",
)

DOC_CAL_AGENDA = _doc(
    purpose="Load everything for the **My events / month calendar** screen: schedule events + goal-plan tasks.",
    inputs=f"{_BEARER}\n\nQuery: `from`, `to` (YYYY-MM-DD). Use first/last day of visible month (include padding days if your grid shows them).",
    outputs=(
        "`from`, `to`, `timezone`, `events[]` (schedule — map to purple), `goal_tasks[]` "
        "(each: `id`, `title`, `task_date`, `start_time`, `end_time`, `status`, `plan_id`, `goal_id`)."
    ),
    flutter="Primary calendar API. Use `goal_tasks[].id` for PATCH/DELETE on `/api/goals/tasks/{id}`.",
)

DOC_CAL_CREATE = _doc(
    purpose="Create a new schedule block (optionally with weekly recurrence).",
    inputs=(
        f"{_BEARER}\n\n"
        "JSON body: `title`, `description`, `start`, `end` (local naive ISO, e.g. `2026-06-03T09:00:00`), "
        "`kind`, `location`, `all_day`, optional `recurrence`: "
        "`enabled`, `weekly_mode` (`same_day` | `weekdays`), `skip_days` (0=Mon…6=Sun), `repeat_weeks`."
    ),
    outputs="`ok`, `events[]` (created rows), `recurrence` (if series), `schedule_version`.",
    flutter="After create, refresh agenda or bump local `schedule_version` from response.",
    errors="400 validation · 401 auth",
)

DOC_CAL_UPDATE = _doc(
    purpose="Edit one existing schedule event (title, times, etc.).",
    inputs=f"{_BEARER}\n\nPath: `event_id` (UUID from agenda/events). JSON body: any of `title`, `description`, `start`, `end`, `kind`, `location`, `all_day` (only send fields to change).",
    outputs="`ok`, `event` (updated row), `schedule_version`.",
    errors="404 unknown event · 401 auth",
)

DOC_CAL_DELETE = _doc(
    purpose="Remove a schedule event, or an entire recurrence series.",
    inputs=f"{_BEARER}\n\nPath: `event_id`. Query: `delete_series` (bool, default false) — true deletes all occurrences in the series.",
    outputs="`ok`, `deleted` (count), `schedule_version`.",
    errors="404 · 401",
)

# --- Goal plan ---

DOC_GOAL_START = _doc(
    purpose="Start a new 7-day goal plan; AI asks follow-up questions.",
    inputs=f"{_BEARER}\n\nJSON body: `achievement` (what user wants — required), optional `title`, `description`.",
    outputs="`plan_id`, `goal_id`, `reply_text` / `assistant_text`, `phase`, `state`, `debug`, `api_usage`.",
    flutter="Save `plan_id` for `/api/goals/plan/message` and `/api/goals/plan/{plan_id}`.",
    errors="422 empty achievement · 401",
)

DOC_GOAL_MESSAGE = _doc(
    purpose="Continue the goal-planning conversation for an existing plan.",
    inputs=f"{_BEARER}\n\nJSON body: `plan_id`, `message`, optional `ui_mode` (`my_goals` | `new_goal`).",
    outputs="`reply_text`, `phase`, `plan_id`, optional `schedule_display` when plan is active, `tool_trace`, `api_usage`.",
    flutter="Chat UI for Goals tab. Same Bearer token as calendar.",
)

DOC_GOAL_LIST = _doc(
    purpose="List all goal week plans for the plan picker / My goals screen.",
    inputs=_BEARER,
    outputs="`plans[]`, `goals[]`, `phase`, `reply_text`, `debug`.",
)

DOC_GOAL_GET = _doc(
    purpose="Load one plan's session state, chat history, and optional week schedule JSON.",
    inputs=f"{_BEARER}\n\nPath: `plan_id`. Query: `include_messages` (bool, default true).",
    outputs="`plan_id`, `session`, `messages[]`, `schedule_display` (when plan active), `bucket_limits`, `phase`, `api_usage`.",
)

# --- Goal tasks ---

DOC_GOAL_TASK_PATCH = _doc(
    purpose="Edit a goal task shown on the calendar (time, title, mark done).",
    inputs=f"{_BEARER}\n\nPath: `task_id` from `GET /api/calendar/agenda`. JSON body (all optional): `title`, `description`, `task_date`, `start_time`, `end_time`, `status` (`pending` | `done` | `skipped`).",
    outputs="`ok`, `task` (updated row).",
    errors="404 · 400 · 401",
)

DOC_GOAL_TASK_DELETE = _doc(
    purpose="Delete a goal task from the calendar.",
    inputs=f"{_BEARER}\n\nPath: `task_id`.",
    outputs="`ok`, `deleted` (task id).",
    errors="404 · 401",
)

# --- System ---

DOC_STATE = _doc(
    purpose="Debug endpoint — agent FSM, storage snapshot, Groq usage (not needed for production Flutter UI).",
    inputs=f"{_BEARER}\n\nQuery: `light` (bool, default true), `user_id` (ignored when token present).",
    outputs="JSON debug object (varies).",
    flutter="Optional; skip unless building diagnostics.",
)

DOC_RESET = _doc(
    purpose="Reset user calendar to seed data and clear chat history (dev/demo).",
    inputs=f"{_BEARER}\n\nJSON body: `user_id` (ignored when token present).",
    outputs="Reset summary JSON from server.",
    flutter="Avoid in production app unless you expose a reset setting.",
)
