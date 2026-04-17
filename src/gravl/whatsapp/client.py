"""Meta WhatsApp Cloud API client — thin httpx wrapper."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from gravl.db.credentials import get_cred

GRAPH_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


class WhatsAppAPIError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]):
        self.status = status
        self.body = body
        err = body.get("error", {}) if isinstance(body, dict) else {}
        super().__init__(
            f"WhatsApp API {status}: code={err.get('code')} "
            f"type={err.get('type')} msg={err.get('message')}"
        )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, WhatsAppAPIError):
        return exc.status == 429 or 500 <= exc.status < 600
    return False


def test_connection() -> dict[str, Any]:
    """Hook for scripts/onboard.py — verifies the phone number ID is reachable."""
    with WhatsAppClient() as c:
        return c.phone_number_info()


class WhatsAppClient:
    def __init__(self, env: str = "prod") -> None:
        self.token = get_cred("meta_whatsapp", "system_user_token", env)
        self.phone_number_id = get_cred("meta_whatsapp", "phone_number_id", env)
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((httpx.TransportError, WhatsAppAPIError)),
        reraise=True,
    )
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(path, json=payload)
        return self._parse(resp)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._client.get(path, params=params or {})
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text}
        if resp.status_code >= 400:
            exc = WhatsAppAPIError(resp.status_code, body)
            if _is_retryable(exc):
                raise exc
            raise exc
        return body

    def phone_number_info(self) -> dict[str, Any]:
        return self._get(f"/{self.phone_number_id}")

    def send_template(
        self,
        to_e164: str,
        template_name: str,
        locale: str,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_e164,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": locale},
            },
        }
        if components:
            payload["template"]["components"] = components
        return self._post(f"/{self.phone_number_id}/messages", payload)
