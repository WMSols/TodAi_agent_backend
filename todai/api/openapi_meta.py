"""OpenAPI metadata — short intro for Swagger / ReDoc."""

from __future__ import annotations

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "auth",
        "description": "Sign-in and first-time setup. **Flutter:** Firebase token only — see each endpoint for inputs/outputs.",
    },
    {
        "name": "chat",
        "description": "Calendar AI agent (natural language scheduling).",
    },
    {
        "name": "calendar",
        "description": "Schedule CRUD and month grid (`/agenda` = events + goal tasks).",
    },
    {
        "name": "goal-plan",
        "description": "7-day goal planner — start plan, chat, list plans.",
    },
    {
        "name": "goal-tasks",
        "description": "Edit/delete goal tasks from the calendar grid.",
    },
    {
        "name": "system",
        "description": "Health, debug, reset.",
    },
]

APP_DESCRIPTION = """
## TodAI API — Flutter developer guide

**Swagger UI:** expand any endpoint below — each one documents **purpose**, **inputs**, and **outputs**.

### Quick start (Flutter)

1. Sign in with **Firebase Auth** (FlutterFire).
2. `GET /api/auth/config` — check `auth_required` and `firebase_project_id`.
3. `POST /api/auth/bootstrap` with header `Authorization: Bearer <id_token>` (once per user).
4. Call other routes with the same header. On **401**, refresh token: `getIdToken(true)`.

### Do not use on Flutter

- `POST /api/auth/login`
- `POST /api/auth/register`

### Main screens → endpoints

| Screen | Endpoint |
|--------|----------|
| Month calendar | `GET /api/calendar/agenda?from=&to=` |
| Edit schedule | `PATCH /api/calendar/events/{id}` |
| Calendar chat | `POST /api/chat` |
| Goals chat | `POST /api/goals/plan/message` |
| Edit goal task on calendar | `PATCH /api/goals/tasks/{id}` |

**Times:** send local naive ISO datetimes (`2026-06-03T09:00:00`); server uses user timezone from profile.
"""

BEARER_SECURITY_DESCRIPTION = (
    "Firebase ID token (Flutter) or JWT from `POST /api/auth/login` (web). "
    "Format: `Authorization: Bearer <token>`"
)
