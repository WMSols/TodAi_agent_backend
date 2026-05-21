"""
Local sandbox entrypoint (gitignored). Run Phase-1 agent from repo root:

    .\\.venv\\Scripts\\python.exe run_sandbox.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import uvicorn


def main() -> None:
    uvicorn.run(
        "todai_sandbox.main:app",
        host="127.0.0.1",
        port=8010,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
