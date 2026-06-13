"""Users repository — users table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_user(conn: sqlite3.Connection, username: str) -> dict | None:
    """Return a user row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def list_users(conn: sqlite3.Connection) -> list[dict]:
    """Return all users.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password_hash: str,
    role: str,
    auth_source: str = "local",
    display_name: str = "",
) -> None:
    """Insert a new user.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_user_role(
    conn: sqlite3.Connection,
    username: str,
    *,
    role: str,
) -> None:
    """Update a user's role.

    # TODO Phase 1
    """
    raise NotImplementedError


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    """Delete a user.

    # TODO Phase 1
    """
    raise NotImplementedError
