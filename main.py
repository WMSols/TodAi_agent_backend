"""
TodAI entrypoint — run from repo root:

    python main.py

Default: full app (UI + /api) from todai.api.main on http://127.0.0.1:8000/

Legacy sandbox (todai_sandbox/): set TODAI_USE_SANDBOX=1
Force backend-only JSON root: set TODAI_BACKEND_ONLY=1
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

HOST = "0.0.0.0"
PORT = 8000


def main() -> None:
    sandbox_dir = _root / "todai_sandbox"
    use_sandbox = os.getenv("TODAI_USE_SANDBOX", "").strip().lower() in ("1", "true", "yes")
    backend_only = os.getenv("TODAI_BACKEND_ONLY", "").strip().lower() in ("1", "true", "yes")
    use_reload = os.getenv("TODAI_RELOAD", "").strip().lower() in ("1", "true", "yes")

    if sandbox_dir.is_dir() and use_sandbox and not backend_only:
        app_path = "todai_sandbox.main:app"
        print("=" * 60, flush=True)
        print("  TodAI LEGACY SANDBOX (TODAI_USE_SANDBOX=1)", flush=True)
        print(f"  Open:  http://127.0.0.1:{PORT}/", flush=True)
        print("=" * 60, flush=True)
    else:
        app_path = "todai.api.main:app"
        print("=" * 60, flush=True)
        print("  TodAI — keep this terminal open while browsing", flush=True)
        print(f"  Open:  http://127.0.0.1:{PORT}/", flush=True)
        print(f"         http://localhost:{PORT}/", flush=True)
        print("  Package: todai/ (api, agent, database)", flush=True)
        if sandbox_dir.is_dir():
            print("  Legacy sandbox still on disk; use TODAI_USE_SANDBOX=1 to run it.", flush=True)
        print("=" * 60, flush=True)

    uvicorn.run(
        app_path,
        host=HOST,
        port=PORT,
        reload=use_reload,
        log_level=os.environ.get("TODAI_LOG_LEVEL", "debug").lower(),
    )


if __name__ == "__main__":
    main()
