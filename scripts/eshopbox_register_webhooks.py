"""Register all Eshopbox webhook events against our receiver.

Usage:
    uv run python scripts/eshopbox_register_webhooks.py \
        --url https://<your-public-host>/webhooks/eshopbox

The script:
  1. Reads `eshopbox.webhook_secret` from the credentials table (set via
     `scripts/onboard.py eshopbox`). If missing, generates one and saves it.
  2. Registers every event listed in docs.eshopbox.com (Inventory + Forward
     Shipment + Return Shipment) pointing to your webhook URL.
  3. Passes `Authorization: Bearer <webhook_secret>` as webhookHeaders so
     the receiver can authenticate incoming deliveries.

Re-running is safe: Eshopbox returns 409/error on duplicates, which we log
but don't fail on.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from gravl.db.adapter import get_connection
from gravl.db.credentials import CredentialNotFound, get_cred
from gravl.integrations.eshopbox.client import EshopboxClient


# (resource, event_subtype, event_type) — lifted verbatim from
# docs.eshopbox.com/advanced/events-webhook
INVENTORY_EVENTS = [
    ("channel_inventory", "Update", "Post"),
]

SHIPMENT_EVENTS = [
    ("shipment", "created", "Put"),
    ("shipment", "packed", "Put"),
    ("shipment", "ready_to_ship", "Put"),
    ("shipment", "picked_up", "Put"),
    ("shipment", "out_for_pickup", "Put"),
    ("shipment", "pickup_failed", "Put"),
    ("shipment", "intransit", "Put"),
    ("shipment", "out_for_delivery", "Put"),
    ("shipment", "delivered", "Put"),
    ("shipment", "failed_delivery", "Put"),
    ("shipment", "rto_created", "Put"),
    ("shipment", "rto_intransit", "Put"),
    ("shipment", "rto_out_for_delivery", "Put"),
    ("shipment", "rto_delivered", "Put"),
    ("shipment", "rto_failed", "Put"),
    ("shipment", "shipment_delayed", "Put"),
    ("shipment", "dispatched", "Put"),
    ("shipment", "shipment_held", "Put"),
    ("shipment", "unhold", "Put"),
    ("shipment", "return_expected", "Put"),
    ("shipment", "cancelled_order", "Put"),
    ("shipment", "ndr_resolution_submitted", "Put"),
    ("shipment", "damage", "Put"),
    ("shipment", "lost", "Put"),
]

RETURN_EVENTS = [
    ("returnShipment", "created", "Put"),
    ("returnShipment", "pickup_pending", "Put"),
    ("returnShipment", "out_for_pickup", "Put"),
    ("returnShipment", "pickup_cancelled", "Post"),
    ("returnShipment", "pickup_failed", "Put"),
    ("returnShipment", "picked_up", "Put"),
    ("returnShipment", "intransit", "Put"),
    ("returnShipment", "out_for_delivery", "Put"),
    ("returnShipment", "delivered", "Put", "v2"),
    ("returnShipment", "delivered_warehouse", "Put", "v2"),
    ("returnShipment", "failed_delivery", "Put"),
    ("returnShipment", "complete", "Put"),
    ("returnShipment", "return_cancelled", "Put"),
    ("returnShipment", "approved", "Put"),
    ("returnShipment", "lost", "Put"),
]

ALL_EVENTS = INVENTORY_EVENTS + SHIPMENT_EVENTS + RETURN_EVENTS


def _ensure_webhook_secret() -> str:
    try:
        return get_cred("eshopbox", "webhook_secret")
    except CredentialNotFound:
        token = secrets.token_urlsafe(32)
        sql = """
            INSERT INTO credentials (integration_id, key, value, env)
            VALUES (
                (SELECT id FROM integrations WHERE slug = %s),
                %s, %s, %s
            )
            ON CONFLICT (integration_id, key, env) DO UPDATE
              SET value = EXCLUDED.value
        """
        with get_connection() as conn:
            conn.execute(sql, ("eshopbox", "webhook_secret", token, "prod"))
        print(f"generated webhook_secret and saved to credentials (len={len(token)})")
        return token


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True,
                    help="public webhook URL, e.g. https://api.gravl.space/webhooks/eshopbox")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    secret = _ensure_webhook_secret()
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}

    if args.dry_run:
        print(f"DRY RUN — would register {len(ALL_EVENTS)} events to {args.url}")
        for row in ALL_EVENTS:
            print(" ", row)
        sys.exit(0)

    ok = 0
    failed = 0
    with EshopboxClient() as c:
        for row in ALL_EVENTS:
            resource, subtype, event_type, *rest = row
            version = rest[0] if rest else "v1"
            try:
                c.register_webhook(
                    resource=resource,
                    event_subtype=subtype,
                    event_type=event_type,
                    version=version,
                    webhook_url=args.url,
                    webhook_headers=headers,
                )
                print(f"  registered: {resource} / {subtype} ({event_type} {version})")
                ok += 1
            except Exception as e:
                print(f"  failed: {resource}/{subtype} — {e}")
                failed += 1

    print(f"\ndone: {ok} registered, {failed} failed")


if __name__ == "__main__":
    main()
