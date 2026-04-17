"""
Meta WhatsApp Cloud API webhook receiver.

Two endpoints mounted under /webhooks/meta/whatsapp:

  GET  → verification handshake. Meta sends ?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…
         We echo hub.challenge only when the token matches the stored `verify_token` credential.

  POST → event delivery. Validates X-Hub-Signature-256 as HMAC-SHA256(app_secret, raw_body),
         logs to webhook_events_audit, then fans out:
           - statuses (sent/delivered/read/failed) → UPDATE whatsapp_sends by meta_message_id
           - inbound messages                    → INSERT bronze_whatsapp_events
         Always returns 200 quickly so Meta doesn't retry; processing is best-effort.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from gravl.db.adapter import get_connection
from gravl.db.credentials import get_cred

router = APIRouter()

ENV = "prod"


@router.get("")
async def verify(
    request: Request,
) -> PlainTextResponse:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    expected = get_cred("meta_whatsapp", "verify_token", ENV)
    if mode == "subscribe" and token == expected:
        return PlainTextResponse(challenge, status_code=200)
    raise HTTPException(status_code=403, detail="verification failed")


@router.post("")
async def receive(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.body()
    if not _signature_valid(raw, x_hub_signature_256):
        _audit(signature_ok=False, path=str(request.url.path))
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _audit(signature_ok=True, path=str(request.url.path))
        raise HTTPException(status_code=400, detail="invalid json")

    _audit(signature_ok=True, path=str(request.url.path))
    _process(payload)
    return {"status": "ok"}


def _signature_valid(raw: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = header.split("=", 1)[1]
    secret = get_cred("meta_whatsapp", "app_secret", ENV).encode()
    mac = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def _audit(*, signature_ok: bool, path: str) -> None:
    sql = """
        INSERT INTO webhook_events_audit
            (integration_id, path, signature_ok)
        VALUES
            ((SELECT id FROM integrations WHERE slug = %s), %s, %s)
    """
    with get_connection() as conn:
        conn.execute(sql, ("meta_whatsapp", path, signature_ok))


def _process(payload: dict[str, Any]) -> None:
    """Fan-out over Meta's nested envelope: entry[].changes[].value.{statuses,messages}."""
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for status in value.get("statuses", []) or []:
                _handle_status(status, value)
            for message in value.get("messages", []) or []:
                _handle_inbound(message, value)


def _handle_status(status: dict[str, Any], value: dict[str, Any]) -> None:
    """Delivery callback: sent | delivered | read | failed."""
    meta_msg_id = status.get("id")
    state = status.get("status")
    if not (meta_msg_id and state):
        return
    upsert = """
        INSERT INTO bronze_whatsapp_events (event_type, external_id, raw_json)
        VALUES ('status', %s, %s::jsonb)
        ON CONFLICT (event_type, external_id) DO UPDATE
          SET raw_json = EXCLUDED.raw_json, received_at = NOW()
    """
    update_send = """
        UPDATE whatsapp_sends
           SET status = %s,
               sent_at = COALESCE(sent_at, NOW())
         WHERE meta_message_id = %s
    """
    with get_connection() as conn:
        conn.execute(upsert, (meta_msg_id, json.dumps({"status": status, "metadata": value.get("metadata")})))
        conn.execute(update_send, (state, meta_msg_id))


def _handle_inbound(message: dict[str, Any], value: dict[str, Any]) -> None:
    msg_id = message.get("id")
    from_e164 = message.get("from")
    sql = """
        INSERT INTO bronze_whatsapp_events (event_type, external_id, from_e164, raw_json)
        VALUES ('message', %s, %s, %s::jsonb)
        ON CONFLICT (event_type, external_id) DO NOTHING
    """
    with get_connection() as conn:
        conn.execute(sql, (msg_id, from_e164, json.dumps({"message": message, "metadata": value.get("metadata")})))
