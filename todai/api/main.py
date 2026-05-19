"""FastAPI application factory (API layer entry)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="TodAI API",
        description="TodAI backend — API layer",
        version="0.1.0",
    )

    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": "todai-api",
                "message": "Backend API initialized. UI is not served here.",
                "health": "/health",
                "docs": "/docs",
            }
        )

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "todai-api"})

    return app


app = create_app()
