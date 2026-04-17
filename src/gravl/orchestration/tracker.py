"""job_tracker helpers."""

from __future__ import annotations

from datetime import datetime

from gravl.db.adapter import get_connection


def start_job(
    integration_slug: str,
    flow: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> int:
    with get_connection() as conn:
        ir = conn.execute(
            "SELECT id FROM integrations WHERE slug = %s", (integration_slug,)
        ).fetchone()
        row = conn.execute(
            """
            INSERT INTO job_tracker (integration_id, flow, window_start, window_end)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (ir["id"], flow, window_start, window_end),
        ).fetchone()
    return row["id"]


def finish_job(job_id: int, rows_landed: int, status: str = "success", error: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE job_tracker
               SET rows_landed = %s, status = %s, error = %s, finished_at = NOW()
             WHERE id = %s
            """,
            (rows_landed, status, error, job_id),
        )
