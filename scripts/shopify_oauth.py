"""
One-shot Shopify OAuth helper.

Runs the OAuth 2.0 Authorization Code Grant flow to obtain a permanent offline
Admin API access token for a single store, then upserts it into `credentials`
as `shopify.admin_token`.

Why this exists: since Jan 1 2026 you can't create new "legacy custom apps"
that expose a shpat_ token in the admin UI. Dev Dashboard apps only give you
client_id + client_secret; the access token must be acquired by completing
the OAuth install flow once. This script does that without any web framework.

Flow:
  1. Reads client_id (api_key) + client_secret (api_secret) from credentials.
  2. Prompts for shop domain + scopes (defaults provided).
  3. Opens a local callback server on 127.0.0.1:8765.
  4. Opens your browser to Shopify's authorize URL.
  5. You click "Install app". Shopify redirects back to the local server with ?code=…
  6. Script verifies HMAC + state, POSTs to /admin/oauth/access_token.
  7. Stores the returned access_token in credentials as `admin_token`.
  8. Runs a live {shop{name plan}} query to prove the token works.

One-time Dev Dashboard prep:
  Open gravl-integration → Versions → New version → URLs → Redirect URLs →
  add `http://127.0.0.1:8765/shopify/callback` → Release.

Run:
  uv run python scripts/shopify_oauth.py
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gravl.db.adapter import get_connection  # noqa: E402
from gravl.db.credentials import get_cred  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
CALLBACK_PATH = "/shopify/callback"
REDIRECT_URI = f"http://{HOST}:{PORT}{CALLBACK_PATH}"

DEFAULT_SCOPES = ",".join([
    "read_orders",
    "read_all_orders",
    "read_products",
    "read_inventory",
    "read_customers",
    "read_fulfillments",
    "read_assigned_fulfillment_orders",
    "read_merchant_managed_fulfillment_orders",
    "read_shipping",
    "read_locations",
    "read_discounts",
    "read_price_rules",
    "read_draft_orders",
    "read_checkouts",
])


# ── HMAC / state verification ───────────────────────────────────

def _verify_hmac(query: dict[str, list[str]], client_secret: str) -> bool:
    """Shopify signs the callback with HMAC-SHA256 over the sorted params (minus hmac)."""
    received = query.get("hmac", [""])[0]
    if not received:
        return False
    pairs = sorted((k, v[0]) for k, v in query.items() if k != "hmac")
    msg = "&".join(f"{k}={v}" for k, v in pairs)
    expected = hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def _verify_shop(shop: str) -> bool:
    """Only accept <something>.myshopify.com to prevent host spoofing."""
    return shop.endswith(".myshopify.com") and "/" not in shop


# ── callback server ─────────────────────────────────────────────

class _CallbackState:
    """Shared mutable state between the server thread and main."""
    code: str | None = None
    shop: str | None = None
    hmac_ok: bool = False
    state_ok: bool = False
    expected_state: str = ""
    client_secret: str = ""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence the default access log
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        q = urllib.parse.parse_qs(parsed.query)
        code = q.get("code", [""])[0]
        shop = q.get("shop", [""])[0]
        state = q.get("state", [""])[0]

        _CallbackState.code = code or None
        _CallbackState.shop = shop or None
        _CallbackState.hmac_ok = _verify_hmac(q, _CallbackState.client_secret)
        _CallbackState.state_ok = hmac.compare_digest(state, _CallbackState.expected_state)

        ok = bool(code and shop and _CallbackState.hmac_ok and _CallbackState.state_ok and _verify_shop(shop))
        body = (
            "<h2>OAuth complete — you can close this tab.</h2>"
            if ok else
            "<h2>OAuth failed — check the terminal for details.</h2>"
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

        # graceful shutdown
        threading.Thread(target=self.server.shutdown, daemon=True).start()


# ── main flow ───────────────────────────────────────────────────

def _token_exchange(shop: str, client_id: str, client_secret: str, code: str) -> dict:
    resp = httpx.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": client_id, "client_secret": client_secret, "code": code},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _upsert_admin_token(access_token: str, shop: str, scope: str) -> None:
    with get_connection() as conn:
        ir = conn.execute("SELECT id FROM integrations WHERE slug='shopify'").fetchone()
        for key, value in (("domain", shop), ("admin_token", access_token), ("scopes", scope)):
            conn.execute(
                """
                INSERT INTO credentials (integration_id, key, value, env, rotated_at)
                VALUES (%s, %s, %s, 'prod', NOW())
                ON CONFLICT (integration_id, key, env) DO UPDATE
                   SET value = EXCLUDED.value, rotated_at = NOW()
                """,
                (ir["id"], key, value),
            )


def _smoke_test() -> None:
    """Prove the token works with a minimal shop query."""
    from gravl.integrations.shopify.client import ShopifyClient  # lazy import

    with ShopifyClient() as c:
        info = c.shop_info()
    print("\n✓ token works. connected to:")
    for k in ("name", "myshopifyDomain", "currencyCode", "ianaTimezone"):
        print(f"    {k:20s} {info.get(k)}")
    print(f"    {'plan':20s} {info['plan']['displayName']}")


def main() -> None:
    # 1. pull client_id + client_secret from credentials
    try:
        client_id = get_cred("shopify", "api_key")
        client_secret = get_cred("shopify", "api_secret")
    except Exception as e:
        raise SystemExit(
            f"missing shopify.api_key / api_secret in credentials — seed them first: {e}"
        )

    # 2. shop domain
    try:
        shop_default = get_cred("shopify", "domain")
    except Exception:
        shop_default = "ydathu-ae.myshopify.com"
    shop = (input(f"\nshop myshopify domain [{shop_default}]: ").strip() or shop_default)
    if not _verify_shop(shop):
        raise SystemExit(f"invalid shop: {shop!r} — must be <name>.myshopify.com")

    # 3. scopes
    scopes = (input(f"\nscopes (comma-separated) [press Enter for defaults]: ").strip() or DEFAULT_SCOPES)

    # 4. CSRF state
    state = secrets.token_urlsafe(16)
    _CallbackState.expected_state = state
    _CallbackState.client_secret = client_secret

    # 5. start callback server
    server = http.server.HTTPServer((HOST, PORT), _Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"\nlistening on http://{HOST}:{PORT}{CALLBACK_PATH}")

    # 6. build authorize URL + open browser
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": scopes,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "grant_options[]": "",  # request offline (long-lived) token
    })
    auth_url = f"https://{shop}/admin/oauth/authorize?{params}"

    print(f"\nopening browser →\n  {auth_url}\n")
    print("if the browser doesn't open, copy that URL manually.\n")
    webbrowser.open(auth_url)

    # 7. wait for the callback
    server_thread.join(timeout=300)  # 5-min window
    if _CallbackState.code is None:
        raise SystemExit("timed out waiting for callback — did you click Install?")

    if not _CallbackState.state_ok:
        raise SystemExit("state mismatch — possible CSRF. aborting.")
    if not _CallbackState.hmac_ok:
        raise SystemExit("hmac mismatch — callback signature invalid. aborting.")
    if not _CallbackState.shop or _CallbackState.shop != shop:
        raise SystemExit(f"shop mismatch — expected {shop}, got {_CallbackState.shop}")

    print("✓ callback received, exchanging code for token…")

    # 8. exchange code for access_token
    payload = _token_exchange(shop, client_id, client_secret, _CallbackState.code)
    access_token = payload["access_token"]
    scope = payload.get("scope", "")
    print(f"✓ got access_token (scope: {scope})")

    # 9. upsert + test
    _upsert_admin_token(access_token, shop, scope)
    print("✓ stored in credentials (shopify.admin_token, shopify.domain, shopify.scopes)")

    _smoke_test()


if __name__ == "__main__":
    main()
