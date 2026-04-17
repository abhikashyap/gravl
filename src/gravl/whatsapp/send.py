"""Single public entry point for outbound WhatsApp.

    from gravl.whatsapp import send_template
    send_template("+919876543210", "order_shipped", {"first_name": "Alex", "order_id": "#1234"})

Writes a `whatsapp_sends` row for every attempt (queued → sent / failed).
"""

from __future__ import annotations

import json
from typing import Any

from gravl.common.logging import get_logger
from gravl.db.adapter import get_connection
from gravl.whatsapp.client import WhatsAppAPIError, WhatsAppClient
from gravl.whatsapp.templates import get_template, render_components

log = get_logger("gravl.whatsapp.send")


def _insert_queued(template_id: int, to_e164: str, variables: dict[str, Any]) -> int:
    sql = """
        INSERT INTO whatsapp_sends (template_id, to_e164, variables_json, status)
        VALUES (%s, %s, %s, 'queued')
        RETURNING id
    """
    with get_connection() as conn:
        row = conn.execute(sql, (template_id, to_e164, json.dumps(variables))).fetchone()
    return row["id"]


def _mark_sent(send_id: int, meta_message_id: str) -> None:
    sql = """
        UPDATE whatsapp_sends
           SET status = 'sent', meta_message_id = %s, sent_at = NOW()
         WHERE id = %s
    """
    with get_connection() as conn:
        conn.execute(sql, (meta_message_id, send_id))


def _mark_failed(send_id: int, error: str) -> None:
    sql = "UPDATE whatsapp_sends SET status = 'failed', error = %s WHERE id = %s"
    with get_connection() as conn:
        conn.execute(sql, (error[:2000], send_id))


def send_template(
    to_e164: str,
    template_name: str,
    variables: dict[str, Any] | None = None,
    locale: str = "en",
    env: str = "prod",
) -> dict[str, Any]:
    """Send a pre-approved WhatsApp template. Returns the Meta API response."""
    variables = variables or {}
    tpl = get_template(template_name, locale)
    if not tpl["approved"]:
        raise RuntimeError(f"template '{template_name}' ({locale}) is not approved")

    components = render_components(tpl["body_json"], variables)
    send_id = _insert_queued(tpl["id"], to_e164, variables)

    try:
        with WhatsAppClient(env=env) as client:
            resp = client.send_template(
                to_e164=to_e164,
                template_name=template_name,
                locale=locale,
                components=components,
            )
    except WhatsAppAPIError as e:
        _mark_failed(send_id, str(e))
        log.error("wa.send.failed", send_id=send_id, to=to_e164, template=template_name, err=str(e))
        raise
    except Exception as e:
        _mark_failed(send_id, repr(e))
        log.error("wa.send.error", send_id=send_id, to=to_e164, template=template_name, err=repr(e))
        raise

    meta_id = (resp.get("messages") or [{}])[0].get("id", "")
    _mark_sent(send_id, meta_id)
    log.info("wa.send.ok", send_id=send_id, to=to_e164, template=template_name, meta_id=meta_id)
    return resp
