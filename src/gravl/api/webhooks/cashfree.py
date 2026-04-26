"""Cashfree webhook receiver.

Cashfree signs payloads with HMAC-SHA256:
  signature = base64( HMAC-SHA256( timestamp + rawBody, webhook_secret ) )
  timestamp comes from header x-webhook-timestamp.

Ref: https://docs.cashfree.com/docs/webhook-signature-verification

Mounted at /webhooks/cashfree.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
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
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.body()

    sig_ok = _verify_signature(raw, x_webhook_timestamp, x_webhook_signature)
    _audit(signature_ok=sig_ok, path=str(request.url.path))

    if not sig_ok:
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    _store(payload)
    return {"status": "ok"}


def _verify_signature(body: bytes, timestamp: str | None, signature: str | None) -> bool:
    if not timestamp or not signature:
        return False
    try:
        secret = get_cred("cashfree", "webhook_secret", ENV)
    except Exception:
        return False
    try:
        message = timestamp.encode() + body
        expected = base64.b64encode(
            hmac.new(secret.encode(), message, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def _audit(*, signature_ok: bool, path: str) -> None:
    sql = """
        INSERT INTO webhook_events_audit
            (integration_id, path, signature_ok)
        VALUES
            ((SELECT id FROM integrations WHERE slug = %s), %s, %s)
    """
    with get_connection() as conn:
        conn.execute(sql, ("cashfree", path, signature_ok))


def _store(payload: dict[str, Any]) -> None:
    """Idempotent insert into bronze_cashfree_events."""
    data = payload.get("data") or {}
    payment = data.get("payment") or {}
    order = data.get("order") or {}
    refund = data.get("refund") or {}

    event_type = payload.get("type") or payload.get("event_type") or "unknown"
    external_id = (
        str(payment.get("cf_payment_id") or "")
        or str(refund.get("cf_refund_id") or "")
        or str(order.get("order_id") or "")
        or None
    )

    sql = """
        INSERT INTO bronze_cashfree_events (event_type, external_id, raw_json)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (event_type, external_id) DO NOTHING
    """
    with get_connection() as conn:
        conn.execute(sql, (event_type, external_id, json.dumps(payload)))
