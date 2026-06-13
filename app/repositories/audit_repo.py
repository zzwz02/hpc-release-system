"""Audit repository — audit table writes and queries.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def log_audit(
    conn: sqlite3.Connection,
    *,
    ts: str,
    user: str,
    role: str,
    app_id: str = "",
    release_id: str = "",
    event: str = "",
    message: str,
    detail: str = "",
) -> None:
    """Append an audit log entry.

    # TODO Phase 1
    """
    raise NotImplementedError


def list_audit(
    conn: sqlite3.Connection,
    *,
    app_id: str | None = None,
    release_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return audit entries, optionally filtered.

    # TODO Phase 1
    """
    raise NotImplementedError
