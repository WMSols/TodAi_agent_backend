"""
TodAI entrypoint — run from repo root:

    python main.py

- Local (todai_sandbox/ present): sandbox UI + /api on http://127.0.0.1:8000/
- GitHub / production clone (no sandbox): backend API on http://127.0.0.1:8000/health

Force backend-only: set TODAI_BACKEND_ONLY=1
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

# 0.0.0.0 avoids "connection refused" when the browser uses localhost → IPv6 (::1)
HOST = "0.0.0.0"
PORT = 8000


def main() -> None:
    sandbox_dir = _root / "todai_sandbox"
    backend_only = os.getenv("TODAI_BACKEND_ONLY", "").strip().lower() in ("1", "true", "yes")
    use_reload = os.getenv("TODAI_RELOAD", "").strip().lower() in ("1", "true", "yes")

    if sandbox_dir.is_dir() and not backend_only:
        app_path = "todai_sandbox.main:app"
        print("=" * 60, flush=True)
        print("  TodAI SANDBOX — keep this terminal open while browsing", flush=True)
        print(f"  Open:  http://127.0.0.1:{PORT}/", flush=True)
        print(f"         http://localhost:{PORT}/", flush=True)
        print("  Terminal debug: ON by default (TODAI_TERMINAL_DEBUG=1)", flush=True)
        print("  Set TODAI_LOG_LEVEL=INFO to reduce noise; DEBUG shows full API JSON", flush=True)
        print("=" * 60, flush=True)
    else:
        app_path = "todai.api.main:app"
        print(f"Backend API — http://127.0.0.1:{PORT}/health", flush=True)

    uvicorn.run(
        app_path,
        host=HOST,
        port=PORT,
        reload=use_reload,
        log_level=os.environ.get("TODAI_LOG_LEVEL", "debug").lower(),
    )


if __name__ == "__main__":
    main()
