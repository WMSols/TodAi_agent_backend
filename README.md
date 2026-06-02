# TodAI Backend

Python **FastAPI** backend — API layer, AI agent, and file-backed storage.

## Repository layout

```
todai/
├── api/                    # HTTP, static UI, logging, middleware
├── agent/                  # Orchestrator, planner (Groq), routing, tools
└── database/               # models, repositories, stores, utils, seed
```

See [todai/README.md](todai/README.md) for the full package map.

## Setup

```powershell
cd f:\TodAi
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `GROQ_API_KEY` for live LLM routing (optional — mock mode works without it).

## Run

From the **repo root** (`F:\TodAi`), not from inside `todai\api`:

```powershell
cd F:\TodAi
.\.venv\Scripts\Activate.ps1
python main.py
```

Other ways that also work:

```powershell
python -m todai.api.main
python todai\api\main.py
```

| URL | Description |
|-----|-------------|
| **http://127.0.0.1:8000/** | Chat UI + `/api/*` |
| **http://127.0.0.1:8000/health** | Liveness + planner mode |

Legacy `todai_sandbox/` (unchanged on disk): `$env:TODAI_USE_SANDBOX = "1"; py -3 main.py`

## Tests

```powershell
py -3 -m pytest tests/ -q
```

## What is pushed to GitHub

| Pushed | Ignored (local only) |
|--------|----------------------|
| `todai/` package | `todai_sandbox/` (legacy) |
| `main.py`, `requirements.txt`, `tests/`, `README.md` | `data/`, `.env`, `.venv/` |
| `.gitignore`, `.env.example` | Root `static/` (use `todai/api/static/`) |
