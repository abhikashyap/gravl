"""Per-stream pull windows.

Replaces opaque `run_state` cursors with a full log of every pull window.
Cursor for the next run is `MAX(window_end)` across all statuses for the
`(integration, stream)` pair. Manual reset = INSERT a row with
`status='manual_reset'` (or DELETE rows to replay).
"""

from __future__ import annotations

from datetime import datetime

from gravl.db.adapter import get_connection


def _integration_id(conn, slug: str) -> int:
    row = conn.execute("SELECT id FROM integrations WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"unknown integration slug: {slug}")
    return row["id"]


def last_window_end(integration_slug: str, stream: str) -> datetime | None:
    """Return the most recent `window_end` for this stream, or None if never pulled."""
    with get_connection() as conn:
        integration_id = _integration_id(conn, integration_slug)
        row = conn.execute(
            """
            SELECT MAX(window_end) AS cursor
              FROM sync_windows
             WHERE integration_id = %s AND stream = %s
            """,
            (integration_id, stream),
        ).fetchone()
    return row["cursor"] if row else None


def record_window(
    integration_slug: str,
    stream: str,
    window_start: datetime | None,
    window_end: datetime,
    records: int,
    s3_uri: str | None,
    status: str = "success",
    notes: str | None = None,
) -> int:
    """INSERT a completed window row; returns new row id."""
    with get_connection() as conn:
        integration_id = _integration_id(conn, integration_slug)
        row = conn.execute(
            """
            INSERT INTO sync_windows
                (integration_id, stream, window_start, window_end,
                 records, s3_uri, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (integration_id, stream, window_start, window_end,
             records, s3_uri, status, notes),
        ).fetchone()
    return row["id"]


def reset_stream(integration_slug: str, stream: str | None = None) -> int:
    """Delete window rows so next run starts from history. Returns rows deleted."""
    with get_connection() as conn:
        integration_id = _integration_id(conn, integration_slug)
        if stream is None:
            cur = conn.execute(
                "DELETE FROM sync_windows WHERE integration_id = %s",
                (integration_id,),
            )
        else:
            cur = conn.execute(
                "DELETE FROM sync_windows WHERE integration_id = %s AND stream = %s",
                (integration_id, stream),
            )
    return cur.rowcount
