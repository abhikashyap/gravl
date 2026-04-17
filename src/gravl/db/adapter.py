"""
Postgres adapter — ported from report_downloader/src/extract/shared/kb/db_adapter.py.

Presents a psycopg2 connection with a sqlite3.Connection-like API so call sites
can write `conn.execute(sql, params).fetchall()` without thinking about the driver.
Translates `?`→`%s` and `datetime('now')`→`CURRENT_TIMESTAMP`.

Reads DATABASE_URL from env (loaded via python-dotenv once).
"""

from __future__ import annotations

import os
import re


def _load_dotenv_once() -> None:
    if getattr(_load_dotenv_once, "_done", False):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    _load_dotenv_once._done = True  # type: ignore[attr-defined]


def _get_database_url() -> str:
    _load_dotenv_once()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Populate gravl/.env.")
    return url


def placeholder() -> str:
    return "%s"


def now_sql() -> str:
    return "NOW()"


# ── SQL translation ──────────────────────────────────────────────

def _translate_sql(sql: str) -> str:
    sql = sql.replace("?", "%s")
    sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP")
    sql = sql.replace("last_insert_rowid()", "lastval()")
    return sql


def _translate_ddl(sql: str) -> str:
    sql = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"datetime\('now'\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    return sql


# ── Row wrapper ──────────────────────────────────────────────────

class DualAccessRow(dict):
    """Dict that also supports integer indexing (sqlite3.Row compat)."""

    def __init__(self, d: dict):
        super().__init__(d)
        self._values = list(d.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _wrap_row(row):
    return None if row is None else DualAccessRow(row)


def _wrap_rows(rows):
    return [DualAccessRow(r) for r in rows]


# ── Cursor / connection wrappers ─────────────────────────────────

class PgCursorWrapper:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params=None):
        self._cur.execute(_translate_sql(sql), params or ())
        return self

    def fetchone(self):
        return _wrap_row(self._cur.fetchone())

    def fetchall(self):
        return _wrap_rows(self._cur.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


class PgConnectionWrapper:
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql: str, params=None):
        cur = self._conn.cursor()
        try:
            cur.execute(_translate_sql(sql), params or ())
        except Exception:
            self._conn.rollback()
            raise
        return PgCursorWrapper(cur)

    def executescript(self, sql: str) -> None:
        """Execute multiple statements separated by `;`."""
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        cur = self._conn.cursor()
        for stmt in statements:
            try:
                cur.execute(_translate_ddl(stmt))
            except Exception as e:
                if "already exists" in str(e).lower():
                    self._conn.rollback()
                    cur = self._conn.cursor()
                    continue
                raise
        self._conn.commit()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            self.commit()
        else:
            self.rollback()
        self.close()


# ── Factory ──────────────────────────────────────────────────────

def get_connection() -> PgConnectionWrapper:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(_get_database_url())
    conn.autocommit = False
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return PgConnectionWrapper(conn)
