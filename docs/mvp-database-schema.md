# TodAI MVP Database Schema

Production database for the **Flutter app** and **calendar AI agent**.  
Stores product data and agent memory only — no debug traces, tool logs, or ML training tables.

**Diagram:** [mvp-database-schema.drawio](./mvp-database-schema.drawio)  
**Engine:** PostgreSQL (recommended) · timestamps in **UTC** · local date/time via `users.timezone`

---

## Overview

| # | Table | Purpose |
|---|--------|---------|
| 1 | `users` | Account and timezone |
| 2 | `user_settings` | Scheduling preferences |
| 3 | `calendar_events` | Schedule source of truth |
| 4 | `conversations` | Chat threads |
| 5 | `messages` | Chat bubbles (user + assistant) |
| 6 | `conversation_context` | Summary of older chat |
| 7 | `agent_memories` | Long-term AI memory & highlights |
| 8 | `agent_turns` | One backend record per user “Send” |

---

## Relationships

| Parent | Child | Cardinality |
|--------|-------|-------------|
| `users` | `user_settings` | 1 : 1 |
| `users` | `calendar_events` | 1 : N |
| `users` | `conversations` | 1 : N |
| `users` | `agent_memories` | 1 : N |
| `conversations` | `messages` | 1 : N |
| `conversations` | `conversation_context` | 1 : 1 |
| `conversations` | `agent_turns` | 1 : N |
| `messages` | `agent_turns` | 1 : 1 (user msg), 0..1 (assistant msg) |

---

## 1. `users`

**What it is:** One row per person using the app.

**Why:** Central account; all calendar, chat, and memory data is scoped by `user_id`.

**Used by:** Flutter (profile), backend (auth), agent (`timezone` for “today” and weekday resolution).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `auth_provider_id` | TEXT | No | External auth ID (Firebase, Apple, Google); unique when set |
| `email` | TEXT | No | Login / account email; unique when set |
| `display_name` | TEXT | No | Shown in app and optional in chat |
| `timezone` | TEXT | Yes | IANA zone (e.g. `Asia/Karachi`) for local dates and agent anchoring |
| `locale` | TEXT | Yes | e.g. `en` — Flutter UI and formatted dates |
| `status` | TEXT | Yes | `active` \| `deleted` |
| `created_at` | TIMESTAMPTZ | Yes | Account created |
| `updated_at` | TIMESTAMPTZ | Yes | Last profile update |

---

## 2. `user_settings`

**What it is:** One row per user — scheduling and agent defaults.

**Why:** Keeps `users` lean; settings change independently of profile.

**Used by:** Flutter (settings), agent (work hours, default duration, free-time logic).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `user_id` | UUID | Yes | PK, FK → `users.id` |
| `working_day_start` | TIME | No | Start of work day (e.g. 08:00) |
| `working_day_end` | TIME | No | End of work day (e.g. 18:00) |
| `working_days` | SMALLINT[] | No | ISO weekdays: 1=Mon … 7=Sun |
| `default_event_duration_minutes` | INT | Yes | Default when user omits duration (e.g. 60) |
| `created_at` | TIMESTAMPTZ | Yes | Row created |
| `updated_at` | TIMESTAMPTZ | Yes | Last settings change |

---

## 3. `calendar_events`

**What it is:** Every block on the user’s calendar.

**Why:** Source of truth for Flutter calendar and agent read/write actions.

**Date / time / day:** Store `start_at` and `end_at` in UTC only. The app computes calendar day and weekday with `users.timezone` (no separate `weekday` column in MVP).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `user_id` | UUID | Yes | FK → `users.id` |
| `title` | TEXT | Yes | Event name |
| `description` | TEXT | No | Optional notes |
| `start_at` | TIMESTAMPTZ | Yes | Start instant (UTC) |
| `end_at` | TIMESTAMPTZ | Yes | End instant (UTC); must be after `start_at` |
| `all_day` | BOOLEAN | Yes | Whole-day event when true |
| `kind` | TEXT | Yes | e.g. `meeting`, `focus`, `personal` |
| `location` | TEXT | No | Place string |
| `source` | TEXT | Yes | `user` \| `agent` |
| `status` | TEXT | Yes | `confirmed` \| `cancelled` |
| `created_at` | TIMESTAMPTZ | Yes | Row created |
| `updated_at` | TIMESTAMPTZ | Yes | Last edit |
| `deleted_at` | TIMESTAMPTZ | No | Soft delete |

**Index (recommended):** `(user_id, start_at)` WHERE `deleted_at IS NULL AND status = 'confirmed'`

---

## 4. `conversations`

**What it is:** A chat thread with the assistant.

**Why:** Groups messages; powers chat list (`title`, `last_message_at`).

**Used by:** Flutter (chat list), backend (open/create thread).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `user_id` | UUID | Yes | FK → `users.id` |
| `title` | TEXT | No | Optional thread label |
| `last_message_at` | TIMESTAMPTZ | No | Sort chat list |
| `created_at` | TIMESTAMPTZ | Yes | Thread opened |
| `archived_at` | TIMESTAMPTZ | No | Hide from default list |

---

## 5. `messages`

**What it is:** Chat bubbles the user sees (`user` or `assistant` only).

**Why:** Chat history in Flutter; agent loads last N messages per turn.

**Rule:** One user message → one orchestration → **one** assistant row. No duplicate rows for router/specialist internals.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `conversation_id` | UUID | Yes | FK → `conversations.id` |
| `user_id` | UUID | Yes | FK → `users.id` (RLS / queries) |
| `role` | TEXT | Yes | `user` \| `assistant` |
| `content` | TEXT | Yes | Message body |
| `created_at` | TIMESTAMPTZ | Yes | Display order |

**Index (recommended):** `(conversation_id, created_at)`

---

## 6. `conversation_context`

**What it is:** One rolling summary of **older** messages in a thread.

**Why:** Long chats exceed the model context window; summary replaces reading hundreds of past rows.

**Used by:** Agent only (not a visible chat bubble).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `conversation_id` | UUID | Yes | PK, FK → `conversations.id` |
| `summary` | TEXT | Yes | Short paragraph of prior context |
| `summary_through_message_id` | UUID | No | FK → `messages.id`; newest message included in summary |
| `updated_at` | TIMESTAMPTZ | Yes | Last summary rebuild |

**Example `summary`:** *User added dance party Saturday 9–10pm. They previewed the week.*

---

## 7. `agent_memories`

**What it is:** Durable AI memory — highlights, preferences, constraints, facts, routines.

**Why:** Agent remembers across days without re-reading full chat. Not the same as `messages` (history vs distilled facts).

**Used by:** Agent each turn; optional Flutter “what AI remembers” screen.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `user_id` | UUID | Yes | FK → `users.id` |
| `memory_type` | TEXT | Yes | See types below |
| `content` | TEXT | Yes | Short natural-language line for the model |
| `importance` | SMALLINT | Yes | 1–10; higher loaded first when trimming context |
| `source` | TEXT | Yes | `user` \| `agent` \| `onboarding` |
| `valid_until` | DATE | No | Optional expiry; NULL = keep until deleted |
| `created_at` | TIMESTAMPTZ | Yes | Stored |
| `updated_at` | TIMESTAMPTZ | Yes | Last edit |
| `deleted_at` | TIMESTAMPTZ | No | Soft delete |

**`memory_type` values:**

| Type | Example |
|------|---------|
| `highlight` | Often schedules gym on Friday evening |
| `preference` | Prefers 9–10pm for social events |
| `constraint` | No meetings before 8am |
| `fact` | Works remotely on Wednesdays |
| `routine` | Weekly team sync Thursday 2pm |

---

## 8. `agent_turns`

**What it is:** Backend link for one “Send” — user message → assistant reply + intent.

**Why:** Idempotency on network retry; product analytics (`intent`); not shown in chat UI.

**Used by:** Backend only.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | UUID | Yes | Primary key |
| `user_id` | UUID | Yes | FK → `users.id` |
| `conversation_id` | UUID | Yes | FK → `conversations.id` |
| `user_message_id` | UUID | Yes | FK → `messages.id`, UNIQUE |
| `assistant_message_id` | UUID | No | FK → `messages.id`, UNIQUE when set |
| `intent` | TEXT | Yes | `chat`, `schedule_read`, `schedule_write`, `schedule_delete` |
| `status` | TEXT | Yes | `completed` \| `failed` |
| `created_at` | TIMESTAMPTZ | Yes | Turn started |
| `completed_at` | TIMESTAMPTZ | No | Turn finished |

---

## What each layer loads

| Layer | Tables |
|-------|--------|
| **Flutter — profile** | `users`, `user_settings` |
| **Flutter — calendar** | `calendar_events` |
| **Flutter — chat** | `conversations`, `messages` |
| **Agent — one turn** | Recent `messages`, `conversation_context`, top `agent_memories`, `calendar_events` (date range), `user_settings`, `users.timezone` |
| **Agent — after turn** | Insert assistant `messages`; update `agent_turns`; mutate `calendar_events`; optional new `agent_memories` |

---

## Example flow: “Add dance party Saturday 9–10pm”

1. Insert `messages` (user).
2. Insert `agent_turns` (in progress).
3. Agent reads memories, context, settings, calendar range.
4. Insert `calendar_events` (`source = agent`, `start_at` / `end_at` from resolved Saturday + times in user TZ).
5. Insert `messages` (assistant): “Added.”
6. Complete `agent_turns` (`intent = schedule_write`, `status = completed`).
7. Optional: insert `agent_memories` (e.g. preference for weekend evenings).

---

## Not in MVP

| Excluded | Reason |
|----------|--------|
| `tool_trace`, router/specialist payloads | Use logs / APM, not product DB |
| Per-HTTP Groq rows / RPM | Metrics service |
| ML training / feature stores | Separate pipeline |
| `agent_memory_embeddings` (pgvector) | Phase 2 semantic search |
| `calendar_connections` (Google sync) | Phase 2 |
| `user_devices` / push tokens | Phase 2 notifications |
| Month JSON shards | Replaced by `calendar_events` rows |

---

## Related docs

- [Orchestrator flow](./orchestrator-flow.drawio) — code path per user message
- [Goals & tasks schema](./goals-tasks-schema.drawio) — goals, weekly plans, tasks synced to calendar
- [Goals & tasks (markdown)](./goals-tasks-schema.md) — column reference for goals tables
- Sandbox today uses JSON under `data/users/`; this schema is the production target
