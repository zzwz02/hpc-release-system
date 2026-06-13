"""QA repository — qa_logs table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_qa_log(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    """Return the QA log record for a release or None."""
    row = conn.execute(
        "SELECT release_id, filename, storage_path, uploaded_at, uploaded_by "
        "FROM qa_logs WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    filename: str,
    storage_path: str,
    uploaded_at: str,
    uploaded_by: str,
) -> None:
    """Insert or replace a QA log record."""
    conn.execute(
        """
        INSERT INTO qa_logs(release_id, filename, storage_path, uploaded_at, uploaded_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          filename=excluded.filename,
          storage_path=excluded.storage_path,
          uploaded_at=excluded.uploaded_at,
          uploaded_by=excluded.uploaded_by
        """,
        (release_id, filename, storage_path, uploaded_at, uploaded_by),
    )


def delete_qa_log(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete the QA log record for a release."""
    conn.execute("DELETE FROM qa_logs WHERE release_id = ?", (release_id,))
