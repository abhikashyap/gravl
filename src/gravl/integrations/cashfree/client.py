"""Cashfree Payments REST client (PG API v2023-08-01).

Auth: every request carries x-client-id, x-client-secret, x-api-version headers.
Environments:
  PROD → https://api.cashfree.com/pg
  TEST → https://sandbox.cashfree.com/pg

Recon API constraints (enforced by Cashfree):
  - max 30-day window per call  → paginate_recon chunks automatically
  - pagination.limit min 10
  - dates must be ISO8601 with timezone offset
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from gravl.db.credentials import get_cred

PROD_BASE = "https://api.cashfree.com/pg"
TEST_BASE = "https://sandbox.cashfree.com/pg"
IST = timezone(timedelta(hours=5, minutes=30))
_MAX_WINDOW_DAYS = 29  # stay under 30-day limit
_RECON_PAGE_SIZE = 200


class CashfreeAPIError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]):
        self.status = status
        self.body = body
        msg = body.get("message") or body.get("error") or str(body)
        super().__init__(f"Cashfree API {status}: {msg}")


def test_connection() -> dict[str, Any]:
    with CashfreeClient() as c:
        return c.test_connection()


class CashfreeClient:
    def __init__(self, env: str = "prod") -> None:
        self.client_id = get_cred("cashfree", "client_id", env)
        self.client_secret = get_cred("cashfree", "client_secret", env)
        self.api_version = get_cred("cashfree", "api_version", env) if _has_cred("cashfree", "api_version", env) else "2023-08-01"
        environment = get_cred("cashfree", "environment", env) if _has_cred("cashfree", "environment", env) else "PROD"
        self.base_url = PROD_BASE if environment == "PROD" else TEST_BASE
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={
                "x-client-id": self.client_id,
                "x-client-secret": self.client_secret,
                "x-api-version": self.api_version,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=30))
    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(path, json=body)
        return self._parse(resp)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=30))
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.get(path, params=params or {})
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text}
        if resp.status_code >= 400:
            raise CashfreeAPIError(resp.status_code, body if isinstance(body, dict) else {"raw": body})
        return body

    @staticmethod
    def _fmt_dt(dt: datetime) -> str:
        """ISO8601 with IST offset — required by Cashfree recon API."""
        return dt.astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")

    def test_connection(self) -> dict[str, Any]:
        now = datetime.now(IST)
        since = now - timedelta(days=1)
        body = self._post("/recon", {
            "filters": {
                "start_date": self._fmt_dt(since),
                "end_date": self._fmt_dt(now),
            },
            "pagination": {"limit": 10, "cursor": None},
        })
        return {
            "connected": True,
            "base_url": self.base_url,
            "api_version": self.api_version,
            "sample_count": len(body.get("data") or []),
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._get(f"/orders/{order_id}")

    def get_order_payments(self, order_id: str) -> list[dict[str, Any]]:
        result = self._get(f"/orders/{order_id}/payments")
        return result if isinstance(result, list) else result.get("data", [])

    def paginate_recon(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield recon rows chunked into ≤29-day windows (Cashfree enforces max 30 days).

        Covers all event types: PAYMENT, REFUND, SETTLEMENT, ADJUSTMENT.
        Cursor-paginated within each window chunk.
        """
        end = (to_dt or datetime.now(IST)).astimezone(IST)
        # Cashfree enforces max 715 days lookback; default to 700 days ago
        _default_start = datetime.now(IST) - timedelta(days=700)
        start = (from_dt or _default_start).astimezone(IST)

        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=_MAX_WINDOW_DAYS), end)
            cursor = None
            while True:
                body = self._post("/recon", {
                    "filters": {
                        "start_date": self._fmt_dt(chunk_start),
                        "end_date": self._fmt_dt(chunk_end),
                    },
                    "pagination": {"limit": _RECON_PAGE_SIZE, "cursor": cursor},
                })
                rows = body.get("data") or []
                if rows:
                    yield rows
                cursor = body.get("cursor")
                if not cursor:
                    break
            chunk_start = chunk_end

    # kept for backward compat — delegates to paginate_recon
    def paginate_settlements(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        per_page: int = 200,
    ) -> Iterator[list[dict[str, Any]]]:
        return self.paginate_recon(from_dt=from_dt, to_dt=to_dt)


def _has_cred(slug: str, key: str, env: str) -> bool:
    try:
        get_cred(slug, key, env)
        return True
    except Exception:
        return False
