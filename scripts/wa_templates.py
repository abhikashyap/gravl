"""
WhatsApp template management CLI.

Commands
--------
  list                    List all templates on Meta WABA (live status)
  submit <name>           Submit a template defined in TEMPLATE_DEFS
  status <name>           Check approval status of a template on Meta
  sync                    Pull approved templates from Meta → local DB
  send <name> <to_e164>   Test-send an approved template (must be in local DB)
  delete <name>           Delete a template from Meta

Usage
-----
  uv run python scripts/wa_templates.py list
  uv run python scripts/wa_templates.py submit gravl_order_confirmed
  uv run python scripts/wa_templates.py sync
  uv run python scripts/wa_templates.py send gravl_order_confirmed +917488337271
  uv run python scripts/wa_templates.py status gravl_order_confirmed
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from gravl.db.adapter import get_connection
from gravl.db.credentials import get_cred

GRAPH_VERSION = "v19.0"
ENV = "prod"

# ── Template catalogue ────────────────────────────────────────────────────────
# Add new templates here. body_json mirrors the shape in gravl.whatsapp.templates.
# 'components' is the raw Meta API payload for submission.
# 'variables' is the sample dict used for test sends.

TEMPLATE_DEFS: dict[str, dict[str, Any]] = {
    "gravl_order_confirmed": {
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}, your Gravl order #{{2}} has been confirmed! We will notify you once it ships. Questions? Just reply here.",
                "example": {"body_text": [["Abhishek", "GRV-1001"]]},
            }
        ],
        "body_json": {"body": {"params": ["first_name", "order_id"]}},
        "sample_variables": {"first_name": "Abhishek", "order_id": "GRV-1001"},
    },
    "gravl_order_shipped": {
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Great news {{1}}! Your Gravl order #{{2}} has shipped. Track your package at {{3}}",
                "example": {
                    "body_text": [["Abhishek", "GRV-1001", "https://track.delhivery.com/123"]]
                },
            }
        ],
        "body_json": {"body": {"params": ["first_name", "order_id", "tracking_url"]}},
        "sample_variables": {
            "first_name": "Abhishek",
            "order_id": "GRV-1001",
            "tracking_url": "https://track.delhivery.com/123",
        },
    },
    "gravl_test_plain": {
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Your Gravl order has been confirmed. Thank you for your purchase.",
            }
        ],
        "body_json": {"body": {"params": []}},
        "sample_variables": {},
    },
}


# ── Meta API helpers ──────────────────────────────────────────────────────────

def _client() -> httpx.Client:
    token = get_cred("meta_whatsapp", "system_user_token", ENV)
    return httpx.Client(
        base_url=f"https://graph.facebook.com/{GRAPH_VERSION}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _waba_id() -> str:
    return get_cred("meta_whatsapp", "waba_id", ENV)


def cmd_list() -> None:
    with _client() as c:
        r = c.get(
            f"/{_waba_id()}/message_templates",
            params={"fields": "name,status,category,language,rejected_reason", "limit": 100},
        )
    rows = r.json().get("data", [])
    if not rows:
        print("No templates on Meta WABA.")
        return
    print(f"{'NAME':<35} {'STATUS':<12} {'CATEGORY':<12} {'LANG':<6} REJECTED_REASON")
    print("-" * 85)
    for t in rows:
        print(
            f"{t['name']:<35} {t['status']:<12} {t['category']:<12} "
            f"{t.get('language',''):<6} {t.get('rejected_reason','')}"
        )


def cmd_submit(name: str) -> None:
    if name not in TEMPLATE_DEFS:
        print(f"ERROR: '{name}' not in TEMPLATE_DEFS. Add it first.")
        sys.exit(1)
    defn = TEMPLATE_DEFS[name]
    payload = {
        "name": name,
        "category": defn["category"],
        "language": defn["language"],
        "components": defn["components"],
    }
    with _client() as c:
        r = c.post(f"/{_waba_id()}/message_templates", json=payload)
    body = r.json()
    print(f"Status: {r.status_code}")
    print(json.dumps(body, indent=2))
    if r.status_code == 200 and body.get("status") not in ("REJECTED",):
        print(f"\n✓ Submitted. Meta status: {body.get('status')}. Run 'status {name}' to track.")
    else:
        print("\n✗ Submission rejected or errored. Check body above.")


def cmd_status(name: str) -> None:
    with _client() as c:
        r = c.get(
            f"/{_waba_id()}/message_templates",
            params={"fields": "name,status,rejected_reason,components", "name": name},
        )
    data = r.json().get("data", [])
    if not data:
        print(f"Template '{name}' not found on Meta.")
        return
    t = data[0]
    print(f"Name:            {t['name']}")
    print(f"Status:          {t['status']}")
    print(f"Rejected reason: {t.get('rejected_reason', 'N/A')}")
    print("Components:")
    print(json.dumps(t.get("components", []), indent=2))


def cmd_sync() -> None:
    """Pull APPROVED templates from Meta and upsert into local DB."""
    with _client() as c:
        r = c.get(
            f"/{_waba_id()}/message_templates",
            params={"fields": "name,status,category,language,id", "limit": 100},
        )
    templates = r.json().get("data", [])
    approved = [t for t in templates if t["status"] == "APPROVED"]

    if not approved:
        print("No APPROVED templates on Meta to sync.")
        return

    synced = 0
    with get_connection() as conn:
        for t in approved:
            name = t["name"]
            lang = t.get("language", "en")
            cat = t["category"]
            meta_id = t["id"]

            # Get body_json from local catalogue if defined, else empty
            body_json = TEMPLATE_DEFS.get(name, {}).get("body_json", {})

            conn.execute(
                """
                INSERT INTO templates (channel, name, category, locale, body_json, meta_template_id, approved)
                VALUES ('whatsapp', %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (channel, name, locale)
                DO UPDATE SET
                    category = EXCLUDED.category,
                    meta_template_id = EXCLUDED.meta_template_id,
                    approved = TRUE,
                    body_json = EXCLUDED.body_json
                """,
                (name, cat, lang, json.dumps(body_json), meta_id),
            )
            print(f"  synced: {name} ({lang}) [{cat}]")
            synced += 1

    print(f"\n✓ Synced {synced} approved template(s) to local DB.")


def cmd_send(name: str, to_e164: str) -> None:
    """Send a template that's already approved in local DB."""
    from gravl.whatsapp.send import send_template

    defn = TEMPLATE_DEFS.get(name, {})
    variables = defn.get("sample_variables", {})
    print(f"Sending '{name}' to {to_e164} with variables: {variables}")
    try:
        resp = send_template(to_e164, name, variables, "en", ENV)
        print("✓ Sent. Meta response:")
        print(json.dumps(resp, indent=2))
    except Exception as e:
        print(f"✗ Failed: {e}")
        sys.exit(1)


def cmd_delete(name: str) -> None:
    with _client() as c:
        r = c.delete(
            f"/{_waba_id()}/message_templates",
            params={"name": name},
        )
    print(f"Status: {r.status_code}")
    print(r.text)


# ── Entrypoint ────────────────────────────────────────────────────────────────

COMMANDS = {
    "list": (cmd_list, 0),
    "submit": (cmd_submit, 1),
    "status": (cmd_status, 1),
    "sync": (cmd_sync, 0),
    "send": (cmd_send, 2),
    "delete": (cmd_delete, 1),
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(0)

    cmd, nargs = COMMANDS[args[0]]
    if len(args) - 1 < nargs:
        print(f"ERROR: '{args[0]}' requires {nargs} argument(s). Got {len(args)-1}.")
        sys.exit(1)

    cmd(*args[1 : 1 + nargs])
