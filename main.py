"""
TodAI entrypoint — run from repo root:

    python main.py

Default: full app (UI + /api) from todai.api.main on http://127.0.0.1:8000/

Enable auto-reload: set TODAI_RELOAD=1
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import uvicorn

from todai.database.config import server_port

HOST = os.environ.get("TODAI_HOST", "0.0.0.0")


def main() -> None:
    use_reload = os.getenv("TODAI_RELOAD", "").strip().lower() in ("1", "true", "yes")
    port = server_port()

    print("=" * 60, flush=True)
    print("  TodAI — keep this terminal open while browsing", flush=True)
    print(f"  Open:  http://127.0.0.1:{port}/", flush=True)
    print(f"         http://localhost:{port}/", flush=True)
    print("  Storage: Supabase (set SUPABASE_* in .env)", flush=True)
    print("=" * 60, flush=True)

    uvicorn.run(
        "todai.api.main:app",
        host=HOST,
        port=port,
        reload=use_reload,
        log_level=os.environ.get("TODAI_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
