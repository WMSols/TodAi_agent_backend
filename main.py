"""
TodAI backend entrypoint — production FastAPI app.

Run from repo root:

    py -3 main.py
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
        "todai.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
