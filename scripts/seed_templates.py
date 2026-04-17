"""
Seed the `templates` table from a Google Sheet.

Expected sheet: worksheet `wa_templates`, header row:
    name | locale | category | body_json | meta_template_id | approved

- `body_json` is a JSON string (see gravl.whatsapp.templates for the shape).
- `approved` is truthy/falsy ("true"/"1"/"yes").

Run:
    uv run python scripts/seed_templates.py
    uv run python scripts/seed_templates.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gravl.db.adapter import get_connection  # noqa: E402


WORKSHEET = "wa_templates"


def _truthy(v: object) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "t"}


def _load_sheet(sheet_id: str, key_path: str):
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(key_path, scopes=scopes)
    return gspread.authorize(creds).open_by_key(sheet_id)


def seed(dry_run: bool = False) -> None:
    load_dotenv()
    sheet_id = os.environ.get("CASHFREE_CONFIG_SHEET_ID")
    key_path = os.environ.get("GOOGLE_SHEETS_KEY_PATH")
    if not (sheet_id and key_path):
        raise SystemExit("CASHFREE_CONFIG_SHEET_ID and GOOGLE_SHEETS_KEY_PATH must be set in .env")
    if not Path(key_path).expanduser().exists():
        raise SystemExit(f"service account JSON not found: {key_path}")

    sheet = _load_sheet(sheet_id, str(Path(key_path).expanduser()))
    try:
        ws = sheet.worksheet(WORKSHEET)
    except Exception:
        raise SystemExit(f"worksheet '{WORKSHEET}' not found in sheet {sheet_id}")

    rows = ws.get_all_records()
    print(f"[{WORKSHEET}] {len(rows)} rows")

    conn = get_connection()
    total = 0
    for r in rows:
        name = str(r.get("name") or "").strip()
        locale = str(r.get("locale") or "en").strip()
        category = str(r.get("category") or "utility").strip()
        body_raw = str(r.get("body_json") or "").strip()
        meta_template_id = str(r.get("meta_template_id") or "").strip() or None
        approved = _truthy(r.get("approved"))

        if not (name and body_raw):
            continue
        try:
            body_json = json.loads(body_raw)
        except json.JSONDecodeError as e:
            print(f"  SKIP {name}/{locale}: invalid body_json — {e}")
            continue

        if dry_run:
            print(f"  (dry) {name}/{locale} cat={category} approved={approved}")
            continue

        conn.execute(
            """
            INSERT INTO templates (channel, name, category, locale, body_json, meta_template_id, approved)
            VALUES ('whatsapp', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (channel, name, locale) DO UPDATE
               SET category = EXCLUDED.category,
                   body_json = EXCLUDED.body_json,
                   meta_template_id = EXCLUDED.meta_template_id,
                   approved = EXCLUDED.approved
            """,
            (name, category, locale, json.dumps(body_json), meta_template_id, approved),
        )
        total += 1

    if not dry_run:
        conn.commit()
    conn.close()
    print(f"upserted {total} template rows" + (" (dry-run)" if dry_run else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
