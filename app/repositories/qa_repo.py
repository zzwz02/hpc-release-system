"""QA repository — qa_logs table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_qa_log(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    """Return public QA log metadata for a release or None."""
    row = conn.execute(
        "SELECT release_id, filename, uploaded_at, uploaded_by, "
        "COALESCE(length(content), 0) AS size_bytes "
        "FROM qa_logs WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_qa_log_content(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[bytes, str] | None:
    """Return ``(content, filename)`` or None when no DB-backed body exists."""
    row = conn.execute(
        "SELECT content, filename FROM qa_logs WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    if not row or row["content"] is None:
        return None
    return bytes(row["content"]), row["filename"]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    filename: str,
    content: bytes,
    uploaded_at: str,
    uploaded_by: str,
) -> None:
    """Insert or replace a QA log with its body embedded in SQLite."""
    conn.execute(
        """
        INSERT INTO qa_logs(
          release_id, filename, content, storage_path, uploaded_at, uploaded_by
        )
        VALUES (?, ?, ?, '', ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          filename=excluded.filename,
          content=excluded.content,
          storage_path='',
          uploaded_at=excluded.uploaded_at,
          uploaded_by=excluded.uploaded_by
        """,
        (release_id, filename, content, uploaded_at, uploaded_by),
    )


def delete_qa_log(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete the QA log record for a release."""
    conn.execute("DELETE FROM qa_logs WHERE release_id = ?", (release_id,))


# ---------------------------------------------------------------------------
# AI suggestions (qa_ai_suggestions)
# ---------------------------------------------------------------------------

def get_ai_suggestions(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT log_filename, result_json, analyzed_by, analyzed_at FROM qa_ai_suggestions WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    if not row:
        return None
    saved = row_to_dict(row)
    saved["result"] = loads_json(saved.pop("result_json"), {})
    return saved


def save_ai_suggestions(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    log_filename: str,
    result: dict[str, Any],
    analyzed_by: str,
    analyzed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO qa_ai_suggestions(release_id, log_filename, result_json, analyzed_by, analyzed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          log_filename=excluded.log_filename,
          result_json=excluded.result_json,
          analyzed_by=excluded.analyzed_by,
          analyzed_at=excluded.analyzed_at
        """,
        (release_id, log_filename, dumps_json(result), analyzed_by, analyzed_at),
    )


def delete_ai_suggestions(conn: sqlite3.Connection, release_id: str) -> int:
    return conn.execute("DELETE FROM qa_ai_suggestions WHERE release_id = ?", (release_id,)).rowcount
