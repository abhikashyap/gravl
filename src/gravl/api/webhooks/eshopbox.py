"""Eshopbox webhook receiver.

Eshopbox does not sign webhook payloads. Instead, whatever headers we declare
when registering the webhook are echoed back on every delivery. We register
with `Authorization: Bearer <webhook_secret>` and validate that header here.

Mounted at /webhooks/eshopbox — same URL handles all resources
(channel_inventory, shipment, returnShipment); we dispatch on body shape.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from gravl.db.adapter import get_connection
from gravl.db.credentials import get_cred

router = APIRouter()

ENV = "prod"


@router.post("")
async def receive(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.body()
    if not _auth_valid(authorization):
        _audit(signature_ok=False, path=str(request.url.path))
        raise HTTPException(status_code=401, detail="invalid auth")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _audit(signature_ok=True, path=str(request.url.path))
        raise HTTPException(status_code=400, detail="invalid json")

    _audit(signature_ok=True, path=str(request.url.path))
    _store(payload)
    return {"status": "ok"}


def _auth_valid(header: str | None) -> bool:
    if not header:
        return False
    try:
        expected = get_cred("eshopbox", "webhook_secret", ENV)
    except Exception:
        return False
    token = header.split(" ", 1)[1] if header.lower().startswith("bearer ") else header
    return token == expected


def _audit(*, signature_ok: bool, path: str) -> None:
    sql = """
        INSERT INTO webhook_events_audit
            (integration_id, path, signature_ok)
        VALUES
            ((SELECT id FROM integrations WHERE slug = %s), %s, %s)
    """
    with get_connection() as conn:
        conn.execute(sql, ("eshopbox", path, signature_ok))


def _store(payload: dict[str, Any]) -> None:
    """Idempotent insert into bronze_eshopbox_events.

    Shapes vary per resource — try a few common id fields for dedup key.
    Requires bronze_eshopbox_events table (currently commented out in
    scripts/init_postgres.sql — uncomment before deploying).
    """
    event_id = (
        payload.get("externalShipmentID")
        or payload.get("customerReturnNumber")
        or payload.get("inventoryItemId")
        or payload.get("id")
    )
    event_type = (
        payload.get("status")
        or payload.get("eventSubType")
        or payload.get("event_type")
    )
    if not event_id:
        return
    sql = """
        INSERT INTO bronze_eshopbox_events (source_event_id, event_type, raw_json)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (source_event_id) DO NOTHING
    """
    with get_connection() as conn:
        conn.execute(sql, (str(event_id), event_type, json.dumps(payload)))
