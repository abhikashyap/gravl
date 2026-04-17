"""
Interactive onboarding wizard.

    uv run python scripts/onboard.py                 # menu: pick an integration
    uv run python scripts/onboard.py shopify         # jump straight to shopify
    uv run python scripts/onboard.py --list          # show what's already registered

For each field in the integration's schema, the wizard prompts you (masking
secrets). Re-runs are idempotent — rows upsert on (integration, key, env).

When a schema declares a `test_hook`, the wizard calls it right after save and
prints the result (e.g. Shopify → shop name + plan) so you know creds work.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gravl.config.integration_schemas import (  # noqa: E402
    SCHEMAS,
    Field,
    IntegrationSchema,
    get_schema,
    list_slugs,
)
from gravl.db.adapter import get_connection  # noqa: E402


# ── terminal helpers ────────────────────────────────────────────

def _prompt(field: Field) -> str | None:
    print(f"\n  {field.prompt_line()}")
    while True:
        raw = getpass("  > ") if field.secret else input("  > ")
        raw = raw.strip()
        if not raw and field.default is not None:
            raw = field.default
        if not raw:
            if field.required:
                print("    (required — please enter a value)")
                continue
            return None
        if field.choices and raw not in field.choices:
            print(f"    (must be one of: {', '.join(field.choices)})")
            continue
        if field.validate:
            err = field.validate(raw)
            if err:
                print(f"    ({err})")
                continue
        return raw


def _yesno(question: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"{question}{suffix} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ── DB writers ──────────────────────────────────────────────────

def _integration_id(conn, slug: str) -> int:
    row = conn.execute("SELECT id FROM integrations WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise SystemExit(
            f"integration '{slug}' not registered. Add to scripts/init_postgres.sql and re-apply."
        )
    return row["id"]


def _upsert_cred(conn, integration_id: int, key: str, value: str, env: str) -> None:
    conn.execute(
        """
        INSERT INTO credentials (integration_id, key, value, env, rotated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (integration_id, key, env) DO UPDATE
           SET value = EXCLUDED.value, rotated_at = NOW()
        """,
        (integration_id, key, value, env),
    )


# ── test hook ───────────────────────────────────────────────────

def _call_test_hook(hook: str) -> None:
    module_path, fn_name = hook.split(":")
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    print("\n  running connection test…")
    try:
        result = fn()
        print("  ✓ connection OK")
        for k, v in (result or {}).items():
            print(f"    {k:20s} {v}")
    except Exception as e:
        print(f"  ✗ connection test failed: {e}")
        print("    credentials were still saved — fix & re-run the wizard to overwrite.")


# ── core flow ───────────────────────────────────────────────────

def onboard_one(schema: IntegrationSchema, env: str) -> None:
    print(f"\n━━ {schema.display_name} ({schema.slug}) ━━")
    if schema.docs_url:
        print(f"   docs: {schema.docs_url}")

    values: dict[str, str] = {}
    for field in schema.fields:
        v = _prompt(field)
        if v is not None:
            values[field.key] = v

    with get_connection() as conn:
        integration_id = _integration_id(conn, schema.slug)
        for key, value in values.items():
            _upsert_cred(conn, integration_id, key, value, env)

    print(f"\n  ✓ saved {len(values)} field(s) to credentials (integration={schema.slug}, env={env})")

    if schema.test_hook:
        _call_test_hook(schema.test_hook)


# ── listing / menu ─────────────────────────────────────────────

def list_everything() -> None:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT i.slug AS integration,
                   COUNT(*) AS n_keys,
                   MAX(c.rotated_at) AS last_update
              FROM credentials c
              JOIN integrations i ON i.id = c.integration_id
             GROUP BY i.slug
             ORDER BY i.slug
            """
        ).fetchall()
    if not rows:
        print("(nothing registered yet — run the wizard without --list)")
        return
    print(f"{'integration':20s}  {'keys':>4s}  last update")
    for r in rows:
        print(f"{r['integration']:20s}  {r['n_keys']:>4d}  {r['last_update']}")


def choose_from_menu() -> str:
    print("\nwhich integration would you like to onboard?")
    for i, slug in enumerate(list_slugs(), 1):
        print(f"  {i}) {slug:16s}  — {SCHEMAS[slug].display_name}")
    while True:
        raw = input("> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(SCHEMAS):
            return list_slugs()[int(raw) - 1]
        if raw in SCHEMAS:
            return raw
        print("  (pick a number or a slug)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("integration", nargs="?", help=f"one of: {', '.join(list_slugs())}")
    ap.add_argument("--env", default="prod", choices=["prod", "staging", "dev"])
    ap.add_argument("--list", action="store_true", help="list what's already registered and exit")
    args = ap.parse_args()

    if args.list:
        list_everything()
        return

    while True:
        slug = args.integration or choose_from_menu()
        onboard_one(get_schema(slug), args.env)

        if args.integration:
            break
        if not _yesno("\nonboard another integration?", default=False):
            break


if __name__ == "__main__":
    main()
