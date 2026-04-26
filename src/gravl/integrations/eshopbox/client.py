"""Eshopbox REST client.

Auth (OAuth2 refresh-token, per docs.eshopbox.com):
  1. Apps → Create a custom app → client_id, client_secret, refresh_token
  2. POST https://auth.myeshopbox.com/api/v1/generateToken with
     {grant_type, client_id, client_secret, refresh_token} → access_token
  3. Call APIs with `Authorization: Bearer <access_token>`

Two API hosts are in play:
  - workspace host: https://<workspace>.myeshopbox.com
      orders/erp, inventoryListing, product-engine, webhook registration
  - WMS host:       https://wms.eshopbox.com
      shipments (forward + return-shipment single lookups)
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from gravl.db.adapter import get_connection
from gravl.db.credentials import get_cred

AUTH_URL = "https://auth.myeshopbox.com/api/v1/generateToken"
WMS_BASE = "https://wms.eshopbox.com"


def _save_refresh_token(new_token: str) -> None:
    """Persist a rotated refresh token back to DB so next boot picks it up."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE credentials
               SET value = %s, rotated_at = NOW()
             WHERE integration_id = (SELECT id FROM integrations WHERE slug = 'eshopbox')
               AND key = 'refresh_token'
               AND env = 'prod'
            """,
            (new_token,),
        )


def test_connection() -> dict[str, Any]:
    with EshopboxClient() as c:
        return c.test_connection()


class EshopboxClient:
    def __init__(self) -> None:
        self.workspace = get_cred("eshopbox", "workspace")
        self.client_id = get_cred("eshopbox", "client_id")
        self.client_secret = get_cred("eshopbox", "client_secret")
        self.refresh_token = get_cred("eshopbox", "refresh_token")
        self.workspace_base = f"https://{self.workspace}.myeshopbox.com"
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._http = httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Auth ──────────────────────────────────────────────────────

    def _mint_token(self) -> None:
        resp = httpx.post(
            AUTH_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body.get("access_token") or body.get("accessToken")
        if not self._access_token:
            raise RuntimeError(f"eshopbox: no access_token in response: {body}")
        expires_in = body.get("expires_in", 2592000)
        self._token_expires_at = time.time() + max(60, int(expires_in) - 60)
        # If Eshopbox rotates the refresh token, persist the new one immediately.
        new_refresh = body.get("refresh_token") or body.get("refreshToken")
        if new_refresh and new_refresh != self.refresh_token:
            self.refresh_token = new_refresh
            _save_refresh_token(new_refresh)

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token or time.time() >= self._token_expires_at:
            self._mint_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Core request ──────────────────────────────────────────────

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=30))
    def _request(self, method: str, url: str, *, params=None, json_body=None, extra_headers=None) -> dict[str, Any]:
        headers = {**self._auth_headers(), "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        resp = self._http.request(method, url, params=params, json=json_body, headers=headers)
        if resp.status_code == 401:
            self._access_token = None
            headers = {**self._auth_headers(), "Accept": "application/json", **(extra_headers or {})}
            resp = self._http.request(method, url, params=params, json=json_body, headers=headers)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    def test_connection(self) -> dict[str, Any]:
        self._mint_token()
        return {
            "workspace": self.workspace,
            "workspace_base": self.workspace_base,
            "wms_base": WMS_BASE,
            "token_acquired": bool(self._access_token),
        }

    # ── Orders (workspace host) ───────────────────────────────────

    def paginate_orders(
        self,
        updated_after_ms: int | None = None,
        fields: str | None = None,
        per_page: int = 100,
    ) -> Iterator[list[dict]]:
        """GET /api/v1/orders/erp — yields pages of `hits`.

        `updated_after_ms` filters on `updatedAt` timestamp in milliseconds.
        `fields` is a comma-separated list; if None, server returns default shape.
        """
        url = f"{self.workspace_base}/api/v1/orders/erp"
        page = 0
        # `filters` is a required query parameter; pass 0 for full scan.
        filters = f"(updatedAt >= {updated_after_ms or 0})"
        while True:
            params: dict[str, Any] = {"page": page, "per_page": per_page, "filters": filters}
            if fields:
                params["fields"] = fields
            body = self._request("GET", url, params=params)
            hits = body.get("hits") or []
            yield hits
            if not body.get("hasNext"):
                return
            page += 1

    # ── Inventory (workspace host) ────────────────────────────────

    def paginate_inventory(self, per_page: int = 100) -> Iterator[list[dict]]:
        """POST /api/v1/inventoryListing — snapshot across all SKUs."""
        url = f"{self.workspace_base}/api/v1/inventoryListing"
        page = 1
        while True:
            body = self._request(
                "POST", url,
                json_body={"page": page, "per_page": per_page, "products": []},
            )
            rows = body.get("hits") or body.get("data") or body.get("products") or []
            yield rows
            has_next = body.get("hasNext")
            if has_next is None:
                has_next = len(rows) == per_page
            if not has_next:
                return
            page += 1

    # ── Shipments (WMS host, Laravel-paginated) ───────────────────

    def paginate_shipments(
        self,
        status: str | None = None,
        expected_ship_date: str | None = None,
        per_page: int = 100,
    ) -> Iterator[list[dict]]:
        """GET wms.eshopbox.com/api/order/shipment — follows next_page_url."""
        url = f"{WMS_BASE}/api/order/shipment"
        params: dict[str, Any] = {"page": 1, "per_page": per_page}
        if status:
            params["status"] = status
        if expected_ship_date:
            params["expectedShipDate"] = expected_ship_date
        while True:
            body = self._request("GET", url, params=params)
            rows = body.get("data") or []
            yield rows
            next_url = body.get("next_page_url")
            if not next_url:
                return
            url = next_url
            params = None

    # ── Webhook registration (workspace host) ─────────────────────

    def register_webhook(
        self,
        resource: str,
        event_subtype: str,
        webhook_url: str,
        webhook_headers: dict[str, str] | None = None,
        event_type: str = "Put",
        version: str = "v1",
        external_channel_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/webhook. Eshopbox doesn't sign payloads; instead
        whatever headers we pass here will be echoed back on every delivery —
        that's the receiver's auth check.
        """
        url = f"{self.workspace_base}/api/v1/webhook"
        payload: dict[str, Any] = {
            "resource": resource,
            "eventType": event_type,
            "eventSubType": event_subtype,
            "version": version,
            "webhookUrl": webhook_url,
            "webhookMethod": "POST",
        }
        if webhook_headers:
            payload["webhookHeaders"] = webhook_headers
        if external_channel_id:
            payload["externalChannelID"] = external_channel_id
        return self._request("POST", url, json_body=payload, extra_headers={"ProxyHost": self.workspace})
