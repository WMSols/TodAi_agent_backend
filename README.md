# TodAI Backend

Python **FastAPI** backend — API layer, AI agent, bundled web UI, and **Supabase** persistence.

## Repository layout

```
todai/
├── api/                    # HTTP, static UI, logging, middleware
├── agent/                  # Orchestrator, planner (Groq), routing, tools
├── calendar_api/           # REST calendar CRUD
├── goal_planner/           # Goal plans, tasks, AI intake
└── database/               # models, repositories, stores, seed (for new users)
```

## Setup (local)

```powershell
cd f:\TodAi
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set:

- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (required)
- `LOCAL_AUTH_SECRET` (web login JWTs)
- `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON` (Flutter app)
- `GROQ_API_KEY` (recommended; mock routing works without it locally)
- `AUTH_DEV_ALLOW_DEFAULT=true` (optional — skip login for local web testing only)

Supabase schema: run migrations under `docs/supabase/` if setting up a new project.

## Run (local)

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

## Deploy on Render (no Docker)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint** and select the repo (`render.yaml`), **or** **New → Web Service** with:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Health check path:** `/health`
3. Set environment variables (see `.env.example`). Required on Render:
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
   - `GROQ_API_KEY`
   - `LOCAL_AUTH_SECRET` (if using bundled web login)
   - `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON` (for Flutter)
   - Do **not** set `AUTH_DEV_ALLOW_DEFAULT=true` in production.
4. Optional: `CORS_ORIGINS` — comma-separated origins for a **separate** web frontend (e.g. Flutter web). The bundled UI at `/` uses same-origin and does not need CORS.
5. Deploy. Open `https://<your-service>.onrender.com/` for the web UI, or point your app at `https://<your-service>.onrender.com/api/...`.

Render sets `PORT` automatically; the app reads `PORT` then `TODAI_PORT` (default 8000).

### Flutter / external app integration

1. Firebase sign-in → `getIdToken()`
2. `POST https://<render-host>/api/auth/bootstrap` with `Authorization: Bearer <token>`
3. All API calls with the same Bearer header

Full endpoint list: `/docs` on the deployed service.

## Tests

```powershell
py -3 -m pytest tests/ -q
```

## What is pushed to GitHub

| Pushed | Ignored (local only) |
|--------|----------------------|
| `todai/` package, `render.yaml`, `.env.example` | `.env`, `.venv/` |
| `main.py`, `requirements.txt`, `README.md` | Root `static/` (use `todai/api/static/`) |
