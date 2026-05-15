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

## Run (backend API)

```powershell
py -3 main.py
```

Health check: **http://127.0.0.1:8000/health**

## Local sandbox (not in Git)

Phase-1 agent work lives in `todai_sandbox/` (gitignored). Run locally with:

```powershell
py -3 run_sandbox.py
```

Sandbox UI: **http://127.0.0.1:8010/** (requires `static/` and `data/` on disk — also gitignored).

## What is pushed to GitHub

| Pushed | Ignored (local only) |
|--------|----------------------|
| `todai/` package + layer folders | `todai_sandbox/` |
| `main.py`, `requirements.txt`, `README.md` | `static/`, `data/`, `tests/`, `docs/` |
| `.gitignore`, `.env.example` | `.env`, `.venv/`, `run_sandbox.py` |
