"""Groq LLM environment settings."""

from __future__ import annotations

import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_groq_ctx = os.environ.get("GROQ_CONTEXT_WINDOW_TOKENS", "").strip()
GROQ_CONTEXT_WINDOW_TOKENS: int | None = int(_groq_ctx) if _groq_ctx.isdigit() else None


def planner_mode() -> str:
    return "groq" if GROQ_API_KEY else "mock"
