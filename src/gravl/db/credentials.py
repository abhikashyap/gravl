"""
Credential lookup from the `credentials` table.

    value = get_cred("shopify", "admin_token")
    all_creds = get_all_creds("cashfree")
    registered = list_registered_integrations()
"""

from __future__ import annotations

from gravl.db.adapter import get_connection


class CredentialNotFound(KeyError):
    pass


def get_cred(integration_slug: str, key: str, env: str = "prod") -> str:
    sql = """
        SELECT c.value
          FROM credentials c
          JOIN integrations i ON i.id = c.integration_id
         WHERE i.slug = %s AND c.key = %s AND c.env = %s
    """
    with get_connection() as conn:
        row = conn.execute(sql, (integration_slug, key, env)).fetchone()
    if row is None:
        raise CredentialNotFound(
            f"credential missing: integration={integration_slug} key={key} env={env}"
        )
    return row["value"]


def get_all_creds(integration_slug: str, env: str = "prod") -> dict[str, str]:
    sql = """
        SELECT c.key, c.value
          FROM credentials c
          JOIN integrations i ON i.id = c.integration_id
         WHERE i.slug = %s AND c.env = %s
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (integration_slug, env)).fetchall()
    return {r["key"]: r["value"] for r in rows}


def list_registered_integrations(env: str = "prod") -> list[str]:
    """Every integration slug that has at least one credential row."""
    sql = """
        SELECT DISTINCT i.slug
          FROM credentials c
          JOIN integrations i ON i.id = c.integration_id
         WHERE c.env = %s
         ORDER BY i.slug
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (env,)).fetchall()
    return [r["slug"] for r in rows]
