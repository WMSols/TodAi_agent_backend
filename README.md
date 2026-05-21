# TodAI Backend

Python **FastAPI** backend — API layer, AI agent, and file-backed storage.

## Repository layout

```
todai/
├── api/                    # HTTP routes, static UI, middleware
│   ├── main.py             # FastAPI app entry
│   ├── service.py          # process_chat, debug state
│   ├── static/index.html   # Chat + debug UI
│   └── middleware/
├── agent/                  # AI agent
│   ├── core/               # Turn orchestration, intents, display
│   ├── planner/            # Groq router + specialist (llm, prompts)
│   ├── routing/            # Intent routing, date scope, guards
│   └── tools/              # Calendar read/write tools
├── memory/                 # Reserved for future memory layers
└── database/               # File storage, seeds, API models
    ├── storage.py
    └── seed/default/
```

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
