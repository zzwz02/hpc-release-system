"""QA repository — qa_logs table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_qa_log(conn: sqlite3.Connection, release_id: str) -> dict | None:
    """Return the QA log record for a release or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def upsert_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    filename: str,
    storage_path: str,
    uploaded_at: str,
    uploaded_by: str,
) -> None:
    """Insert or replace a QA log record.

    # TODO Phase 1
    """
    raise NotImplementedError
