"""
FastAPI entrypoint.

    uv run uvicorn gravl.api.main:app --reload --port 8000

Mounts webhook routers under /webhooks/*.
"""

from __future__ import annotations

from fastapi import FastAPI

from gravl.api.webhooks import meta_whatsapp

app = FastAPI(title="gravl", version="0.1.0")

app.include_router(meta_whatsapp.router, prefix="/webhooks/meta/whatsapp", tags=["webhooks"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
