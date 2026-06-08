"""OpenAPI metadata for Swagger / ReDoc."""

from __future__ import annotations

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "auth", "description": "Sign-in and bootstrap."},
    {"name": "chat", "description": "Calendar AI chat."},
    {"name": "calendar", "description": "Calendar data and events."},
    {"name": "goal-plan", "description": "Goal plans: start, message, list, get."},
    {"name": "goal-tasks", "description": "Edit goal tasks on the calendar."},
    {"name": "system", "description": "Health and debug."},
]

APP_DESCRIPTION = """
## TodAI API (Flutter app)

1. Firebase sign-in → `getIdToken()`
2. `POST /api/auth/bootstrap` (header only, once)
3. All other calls: `Authorization: Bearer <token>`

**Auth on Flutter app:** Firebase token only. Do not call `/api/auth/login` or `/api/auth/register`.

**IDs:** `plan_id` = 7-day plan (use in goal chat). `goal_id` = parent goal. Different values.

| Screen | Endpoint |
|--------|----------|
| After login | `POST /api/auth/bootstrap` |
| Calendar chat | `POST /api/chat` |
| Month view | `GET /api/calendar/agenda?from=&to=` |
| New goal | `POST /api/goals/plan/start` |
| Goal chat | `POST /api/goals/plan/message` |
| Goal list | `GET /api/goals/plan/plans` |
| Edit goal task | `PATCH /api/goals/tasks/{id}` |

Event times: `2026-06-03T09:00:00` (local, no timezone in string).
"""

BEARER_SECURITY_DESCRIPTION = "Authorization: Bearer <token> (Firebase ID token on the Flutter app)"
