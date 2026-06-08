"""Per-endpoint OpenAPI descriptions."""

from __future__ import annotations

_BEARER = "**Header:** `Authorization: Bearer <token>` (Firebase ID token from the Flutter app)"


def _doc(
    *,
    purpose: str,
    inputs: str,
    outputs: str,
    note: str | None = None,
    errors: str | None = None,
) -> str:
    parts = [f"**What it does:** {purpose}", f"**Send:** {inputs}", f"**You get back:** {outputs}"]
    if note:
        parts.append(f"**Note:** {note}")
    if errors:
        parts.append(f"**Errors:** {errors}")
    return "\n\n".join(parts)


DOC_HEALTH = _doc(
    purpose="Check if the server is running.",
    inputs="Nothing.",
    outputs="`ok`, `storage`, `firebase_configured`, `auth_required`, and related flags.",
)

DOC_AUTH_CONFIG = _doc(
    purpose="Read server auth settings.",
    inputs="Nothing.",
    outputs="`auth_required`, `providers.firebase`, `providers.local`, `firebase_project_id`.",
)

DOC_AUTH_REGISTER = _doc(
    purpose="Create a web account (not used by the Flutter app).",
    inputs="Body: `display_name`, `password`, optional `email`.",
    outputs="`access_token`, `user`.",
    errors="400 · 503",
)

DOC_AUTH_LOGIN = _doc(
    purpose="Web login (not used by the Flutter app).",
    inputs="Body: `username`, `password`.",
    outputs="`access_token`, `user`.",
    errors="401",
)

DOC_AUTH_BOOTSTRAP = _doc(
    purpose="Create the user in TodAI after first sign-in.",
    inputs=_BEARER,
    outputs="`ok`, `user_id`, `display_name`, `email`, `storage`.",
    errors="401",
)

DOC_CHAT = _doc(
    purpose="Calendar AI chat.",
    inputs=f"{_BEARER}\n\nBody: `message` (required). `user_id` is ignored when the token header is sent.",
    outputs="`assistant_text`, `state`, `schedule_version`, optional `schedule_display`, optional `pending_proposal_id`.",
    errors="401 · 404 (call bootstrap first)",
)

DOC_CAL_LIST_EVENTS = _doc(
    purpose="Load schedule events for a date range (no goal tasks).",
    inputs=f"{_BEARER}\n\nQuery: `from`, `to` (`YYYY-MM-DD`).",
    outputs="`events[]`, `timezone`.",
)

DOC_CAL_AGENDA = _doc(
    purpose="Month calendar: schedule events and goal tasks.",
    inputs=f"{_BEARER}\n\nQuery: `from`, `to` (`YYYY-MM-DD`).",
    outputs="`events[]`, `goal_tasks[]` (`id`, `task_date`, `status`, …), `timezone`.",
)

DOC_CAL_CREATE = _doc(
    purpose="Create a calendar event.",
    inputs=f"{_BEARER}\n\nBody: `title`, `start`, `end` (e.g. `2026-06-03T09:00:00`), optional `description`, `recurrence`.",
    outputs="`ok`, `events[]`, `schedule_version`.",
    note="Recurrence `skip_days`: 0=Monday … 6=Sunday.",
)

DOC_CAL_UPDATE = _doc(
    purpose="Update an event.",
    inputs=f"{_BEARER}\n\nPath: `event_id`. Body: fields to change only.",
    outputs="`ok`, `event`, `schedule_version`.",
)

DOC_CAL_DELETE = _doc(
    purpose="Delete an event.",
    inputs=f"{_BEARER}\n\nPath: `event_id`. Query `delete_series=true` to delete a full repeat series.",
    outputs="`ok`, `deleted`, `schedule_version`.",
)

DOC_GOAL_START = _doc(
    purpose="Start a new 7-day goal plan.",
    inputs=f"{_BEARER}\n\nBody: at least one of `achievement`, `description`, or `title`.",
    outputs="`plan_id`, `goal_id`, `reply_text`, `phase`, `start_date`, `end_date`.",
    note="`plan_id` and `goal_id` are different. Use `plan_id` in goal chat.",
    errors="422 if all text fields empty",
)

DOC_GOAL_MESSAGE = _doc(
    purpose="Goal chat for one plan.",
    inputs=f"{_BEARER}\n\nBody: `plan_id`, `message`, optional `ui_mode` (`my_goals` or `new_goal`).",
    outputs="`reply_text`, `plan_id`, optional `schedule_display`.",
    note="Not the same as `GET /plans` (that endpoint lists plans, no chat body).",
)

DOC_GOAL_LIST = _doc(
    purpose="List all goals and week plans.",
    inputs=f"{_BEARER}\n\nNo body.",
    outputs="`plans[]`, `goals[]`.",
)

DOC_GOAL_GET = _doc(
    purpose="Load one plan: session, messages, schedule.",
    inputs=f"{_BEARER}\n\nPath: `plan_id`. Query `include_messages` (default true).",
    outputs="`plan_id`, `session`, `messages[]`, optional `schedule_display`.",
)

DOC_GOAL_TASK_PATCH = _doc(
    purpose="Update a goal task.",
    inputs=f"{_BEARER}\n\nPath: `task_id`. Body: optional `title`, `task_date`, `start_time`, `end_time`, `status`.",
    outputs="`ok`, `task`.",
)

DOC_GOAL_TASK_DELETE = _doc(
    purpose="Delete a goal task.",
    inputs=f"{_BEARER}\n\nPath: `task_id`.",
    outputs="`ok`, `deleted`.",
)

DOC_STATE = _doc(
    purpose="Server debug state.",
    inputs=f"{_BEARER}\n\nQuery `light` (default true).",
    outputs="`state`, `schedule_version`, `api_usage`, `storage_index`, …",
)

DOC_RESET = _doc(
    purpose="Reset user data to seed.",
    inputs=f"{_BEARER}\n\nBody: `user_id` ignored when token is sent.",
    outputs="Summary JSON.",
)
