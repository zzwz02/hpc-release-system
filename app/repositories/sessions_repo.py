"""Sessions repository — sessions table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def create_session(
    conn: sqlite3.Connection,
    *,
    token: str,
    username: str,
    created_at: str,
) -> None:
    """Insert a new session row.

    # TODO Phase 1
    """
    raise NotImplementedError


def get_session(conn: sqlite3.Connection, token: str) -> dict | None:
    """Return the session row (with user join) or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    """Delete a session row.

    # TODO Phase 1
    """
    raise NotImplementedError
