# TodAI Backend

Python **FastAPI** backend repository — architectural layout for API, AI Agent, Memory, and Database layers.

## Repository layout

```
todai/
├── api/                    # API layer
│   ├── main.py             # FastAPI app entry
│   ├── routes/
│   ├── dependencies/
│   ├── schemas/
│   └── middleware/
├── agent/                  # AI Agent Core
│   ├── core/
│   ├── planner/
│   ├── tools/
│   ├── routing/
│   └── contracts/
├── memory/                 # Memory System
│   ├── stores/
│   ├── context/
│   └── session/
└── database/               # Database Layer
    ├── models/
    ├── repositories/
    ├── migrations/
    └── session/
```

## Setup

```powershell
cd f:\TodAi
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
py -3 main.py
```

| Your machine | URL |
|--------------|-----|
| **Local** (`todai_sandbox/` exists) | **http://127.0.0.1:8000/** — sandbox UI + `/api/*` |
| **GitHub clone** (no sandbox folder) | **http://127.0.0.1:8000/health** — backend API only |

Force backend-only locally: `$env:TODAI_BACKEND_ONLY = "1"; py -3 main.py`

Alternate sandbox entry (same app, port 8010): `py -3 run_sandbox.py`

## What is pushed to GitHub

| Pushed | Ignored (local only) |
|--------|----------------------|
| `todai/` package + layer folders | `todai_sandbox/` |
| `main.py`, `requirements.txt`, `README.md` | `static/`, `data/`, `tests/`, `docs/` |
| `.gitignore`, `.env.example` | `.env`, `.venv/`, `run_sandbox.py` |
