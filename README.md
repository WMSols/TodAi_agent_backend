# TodAI Backend

Python **FastAPI** backend — API layer, AI agent, and **Supabase** persistence.

## Repository layout

```
todai/
├── api/                    # HTTP, static UI, logging, middleware
├── agent/                  # Orchestrator, planner (Groq), routing, tools
├── calendar_api/           # REST calendar CRUD
├── goal_planner/           # Goal plans, tasks, AI intake
└── database/               # models, repositories, stores, seed (for new users)
```

## Setup

```powershell
cd f:\TodAi
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env` and set:

- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (database — required)
- `LOCAL_AUTH_SECRET` (long random string — signs web login JWTs)
- `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON` (Flutter login)
- `AUTH_DEV_ALLOW_DEFAULT=true` (optional — skip login for local web testing)
- `GROQ_API_KEY` (optional — mock routing works without it)

Run Supabase migrations under `docs/supabase/` including `003_local_auth.sql`.

Run Supabase migrations under `docs/supabase/` (message buckets, calendar events, goals).

## Run

From the **repo root**:

```powershell
python main.py
```

| URL | Description |
|-----|-------------|
| **http://127.0.0.1:8000/** | Web chat UI |
| **http://127.0.0.1:8000/docs** | Swagger API docs (Flutter integration guide) |
| **http://127.0.0.1:8000/redoc** | ReDoc API reference |
| **http://127.0.0.1:8000/health** | Liveness + auth flags |

## Tests

```powershell
py -3 -m pytest tests/ -q
```

## What is pushed to GitHub

| Pushed | Ignored (local only) |
|--------|----------------------|
| `todai/` package | `.env`, `.venv/` |
| `main.py`, `requirements.txt`, `tests/`, `README.md` | Root `static/` (use `todai/api/static/`) |
