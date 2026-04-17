"""
Seed the `credentials` table from a Google Sheet — alternative to the
interactive `onboard.py` wizard.

Expected sheet: one worksheet per integration slug (e.g. 'shopify', 'cashfree'),
header row: `key | value | env`. `env` defaults to prod.

Run:
    uv run python scripts/seed_from_sheet.py
    uv run python scripts/seed_from_sheet.py --worksheet cashfree
    uv run python scripts/seed_from_sheet.py --dry-run

Requires GOOGLE_SHEETS_KEY_PATH and CASHFREE_CONFIG_SHEET_ID in .env.
Share the sheet with the service-account email found inside the key JSON.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gravl.db.adapter import get_connection  # noqa: E402


def _load_sheet(sheet_id: str, key_path: str):
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(key_path, scopes=scopes)
    return gspread.authorize(creds).open_by_key(sheet_id)


def _integration_id(conn, slug: str) -> int | None:
    row = conn.execute("SELECT id FROM integrations WHERE slug = %s", (slug,)).fetchone()
    return row["id"] if row else None


def seed(dry_run: bool = False, only_worksheet: str | None = None) -> None:
    load_dotenv()
    sheet_id = os.environ.get("CASHFREE_CONFIG_SHEET_ID")
    key_path = os.environ.get("GOOGLE_SHEETS_KEY_PATH")
    if not (sheet_id and key_path):
        raise SystemExit("CASHFREE_CONFIG_SHEET_ID and GOOGLE_SHEETS_KEY_PATH must be set in .env")
    if not Path(key_path).expanduser().exists():
        raise SystemExit(f"service account JSON not found: {key_path}")

    sheet = _load_sheet(sheet_id, str(Path(key_path).expanduser()))
    conn = get_connection()

    total = 0
    for ws in sheet.worksheets():
        if only_worksheet and ws.title != only_worksheet:
            continue
        integration_id = _integration_id(conn, ws.title)
        if integration_id is None:
            print(f"[{ws.title}] skipped — no integration with that slug")
            continue
        rows = ws.get_all_records()
        print(f"[{ws.title}] {len(rows)} rows")
        for r in rows:
            key = str(r.get("key") or "").strip()
            value = str(r.get("value") or "").strip()
            env = str(r.get("env") or "prod").strip()
            if not (key and value):
                continue
            if dry_run:
                print(f"  (dry) {ws.title}.{key}[{env}] = {value[:8]}…")
                continue
            conn.execute(
                """
                INSERT INTO credentials (integration_id, key, value, env, rotated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (integration_id, key, env) DO UPDATE
                   SET value = EXCLUDED.value, rotated_at = NOW()
                """,
                (integration_id, key, value, env),
            )
            total += 1
    if not dry_run:
        conn.commit()
    conn.close()
    print(f"upserted {total} credential rows" + (" (dry-run)" if dry_run else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--worksheet")
    args = ap.parse_args()
    seed(dry_run=args.dry_run, only_worksheet=args.worksheet)


if __name__ == "__main__":
    main()
