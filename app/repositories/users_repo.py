"""Users repository — users table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_user(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    """Return a user row or None."""
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return row_to_dict(row) if row else None


def list_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all users ordered by auth_source, role, username."""
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT username, role, auth_source, display_name "
            "FROM users ORDER BY auth_source, role, username"
        )
    ]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password_hash: str,
    role: str,
    auth_source: str = "local",
    display_name: str = "",
) -> None:
    """Insert a new user (ignores conflict — use upsert_ldap_user for LDAP)."""
    conn.execute(
        "INSERT OR IGNORE INTO users(username, password_hash, role, auth_source, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, password_hash, role, auth_source, display_name),
    )


def upsert_ldap_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    role: str,
    display_name: str,
) -> None:
    """Insert or update an LDAP user (first-login auto-create)."""
    conn.execute(
        "INSERT INTO users(username, password_hash, role, auth_source, display_name) "
        "VALUES (?, '', ?, 'ldap', ?) "
        "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name",
        (username, role, display_name),
    )


def update_display_name(
    conn: sqlite3.Connection,
    username: str,
    *,
    display_name: str,
) -> None:
    """Update a user's display_name (e.g. refreshed from LDAP)."""
    conn.execute(
        "UPDATE users SET display_name = ? WHERE username = ?",
        (display_name, username),
    )


def update_user_role(
    conn: sqlite3.Connection,
    username: str,
    *,
    role: str,
) -> None:
    """Update a user's role."""
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))


def update_password_hash(
    conn: sqlite3.Connection,
    username: str,
    *,
    password_hash: str,
) -> None:
    """Update a user's password hash."""
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (password_hash, username),
    )


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    """Delete a user (cascades sessions via FK)."""
    conn.execute("DELETE FROM users WHERE username = ?", (username,))


def display_name_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {username: display_name} for all users with a non-empty name."""
    return {
        row["username"]: str(row["display_name"] or "").strip()
        for row in conn.execute(
            "SELECT username, display_name FROM users WHERE display_name != ''"
        )
        if str(row["display_name"] or "").strip()
    }


def display_names_for(
    conn: sqlite3.Connection,
    usernames: list[str],
) -> dict[str, str]:
    """Return {username: display_name} for a list of usernames (batch load)."""
    if not usernames:
        return {}
    rows = conn.execute(
        "SELECT username, display_name FROM users WHERE username IN ({})".format(
            ",".join("?" * len(usernames))
        ),
        usernames,
    ).fetchall()
    return {r["username"]: r["display_name"] for r in rows}
