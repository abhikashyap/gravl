"""
Declarative schemas for every integration gravl talks to.

Single source of truth for:
  - `scripts/onboard.py`       → which fields to prompt for
  - `scripts/seed_from_sheet.py` → validates sheet rows (future)
  - future admin UI            → renders forms from this declaration

Adding a new integration = one block here + one row in init_postgres.sql seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Field:
    key: str                       # stored as credentials.key
    label: str                     # prompt label
    secret: bool = False           # mask input with getpass
    required: bool = True
    help: str = ""                 # one-liner shown above the prompt
    default: str | None = None
    choices: tuple[str, ...] | None = None
    validate: Callable[[str], str | None] | None = None  # returns error msg or None

    def prompt_line(self) -> str:
        bits = [self.label]
        if self.help:
            bits.append(f" — {self.help}")
        if self.default is not None:
            bits.append(f"  [default: {self.default}]")
        if self.choices:
            bits.append(f"  (one of: {', '.join(self.choices)})")
        if not self.required:
            bits.append("  (optional, press Enter to skip)")
        return "".join(bits)


@dataclass(frozen=True)
class IntegrationSchema:
    slug: str                              # must match integrations.slug in DB
    display_name: str
    docs_url: str
    fields: tuple[Field, ...]
    # dotted "module.path:function_name" — called as fn() -> dict on success.
    test_hook: str | None = None


# ── validators ──────────────────────────────────────────────────

def _is_shopify_domain(v: str) -> str | None:
    v = v.strip().lower()
    if not v.endswith(".myshopify.com"):
        return "expected <shop>.myshopify.com"
    return None


# ── schemas ─────────────────────────────────────────────────────

SCHEMAS: dict[str, IntegrationSchema] = {
    "shopify": IntegrationSchema(
        slug="shopify",
        display_name="Shopify (Admin API)",
        docs_url="https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/generate-app-access-tokens-admin",
        fields=(
            Field("domain", "Shop myshopify domain", help="e.g. my-brand.myshopify.com",
                  validate=_is_shopify_domain),
            Field("admin_token", "Admin API access token", secret=True, help="starts with shpat_"),
            Field("api_key", "Admin API key", secret=True, required=False),
            Field("api_secret", "Admin API secret", secret=True, required=False),
            Field("webhook_secret", "Webhook signing secret", secret=True, required=False,
                  help="set later when you register webhooks"),
            Field("storefront_token", "Storefront API token", secret=True, required=False),
        ),
        test_hook="gravl.integrations.shopify.client:test_connection",
    ),
    "cashfree": IntegrationSchema(
        slug="cashfree",
        display_name="Cashfree Payments",
        docs_url="https://docs.cashfree.com/docs/payment-gateway-dashboard-api-keys",
        fields=(
            Field("client_id", "Cashfree client_id (x-client-id)", secret=True,
                  help="from dashboard → Developers → API Keys"),
            Field("client_secret", "Cashfree client_secret (x-client-secret)", secret=True),
            Field("api_version", "API version header", default="2023-08-01", required=False),
            Field("environment", "Environment", choices=("PROD", "TEST"), default="PROD"),
            Field("merchant_id", "Merchant ID", required=False),
            Field("webhook_secret", "Webhook signing secret", secret=True, required=False),
        ),
    ),
    "freshdesk": IntegrationSchema(
        slug="freshdesk",
        display_name="Freshdesk (support)",
        docs_url="https://developers.freshdesk.com/api/",
        fields=(
            Field("domain", "Freshdesk domain", help="e.g. mybrand.freshdesk.com"),
            Field("api_key", "API key (Profile Settings → View API key)", secret=True),
        ),
    ),
    "eshopbox": IntegrationSchema(
        slug="eshopbox",
        display_name="Eshopbox (WMS)",
        docs_url="https://docs.eshopbox.com/",
        fields=(
            Field("workspace", "Workspace slug",
                  help="the <workspace> in https://<workspace>.myeshopbox.com"),
            Field("client_id", "App client_id", secret=True,
                  help="Apps → Create a custom app → copy client_id"),
            Field("client_secret", "App client_secret", secret=True),
            Field("refresh_token", "App refresh_token", secret=True,
                  help="used to mint access tokens at auth.myeshopbox.com"),
            Field("webhook_secret", "Webhook shared secret", secret=True, required=False,
                  help="optional — set if you configure one when registering the webhook"),
        ),
        test_hook="gravl.integrations.eshopbox.client:test_connection",
    ),
    "meta_whatsapp": IntegrationSchema(
        slug="meta_whatsapp",
        display_name="Meta WhatsApp Cloud API",
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
        fields=(
            Field("system_user_token", "Permanent System User token", secret=True,
                  help="Business Settings → System Users → whatsapp_business_messaging"),
            Field("phone_number_id", "Phone number ID", help="15–16 digits, from WhatsApp Manager"),
            Field("waba_id", "WhatsApp Business Account ID", required=False),
            Field("app_id", "Meta App ID", required=False),
            Field("verify_token", "Webhook verify token", secret=True, required=False,
                  help="any random string; echoed back during subscription"),
        ),
        test_hook="gravl.whatsapp.client:test_connection",
    ),
    "google_sheets": IntegrationSchema(
        slug="google_sheets",
        display_name="Google Sheets (config source)",
        docs_url="https://docs.gspread.org/en/latest/oauth2.html",
        fields=(
            Field("service_account_path", "Path to service account JSON",
                  help="e.g. ./secrets/google_sheets_key.json",
                  default="./secrets/google_sheets_key.json"),
            Field("cashfree_config_sheet_id", "Config sheet ID", required=False),
        ),
    ),
}


def get_schema(slug: str) -> IntegrationSchema:
    if slug not in SCHEMAS:
        raise KeyError(f"no schema for integration '{slug}'. Known: {sorted(SCHEMAS)}")
    return SCHEMAS[slug]


def list_slugs() -> list[str]:
    return list(SCHEMAS.keys())
