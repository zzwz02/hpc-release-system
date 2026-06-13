"""Sessions repository — sessions table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_session(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """Return the session joined with user info, or None if not found.

    Returns: {token, username, role, display_name, auth_source, created_at}
    """
    row = conn.execute(
        """
        SELECT sessions.token, sessions.username, sessions.created_at,
               users.role, users.display_name, users.auth_source
        FROM sessions
        JOIN users ON users.username = sessions.username
        WHERE sessions.token = ?
        """,
        (token,),
    ).fetchone()
    return row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_session(
    conn: sqlite3.Connection,
    *,
    token: str,
    username: str,
    created_at: str,
) -> None:
    """Insert a new session row."""
    conn.execute(
        "INSERT INTO sessions(token, username, created_at) VALUES (?, ?, ?)",
        (token, username, created_at),
    )


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    """Delete a session row (logout)."""
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_all_sessions_for_user(conn: sqlite3.Connection, username: str) -> None:
    """Delete all sessions for a user (e.g. on user delete / role change)."""
    conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
