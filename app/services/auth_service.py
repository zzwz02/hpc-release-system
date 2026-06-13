"""Authentication service — login, logout, session management, password change.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def login(conn: sqlite3.Connection, username: str, password: str) -> dict:
    """Verify credentials (local or LDAP) and create a session.

    # TODO Phase 2
    """
    raise NotImplementedError


def logout(conn: sqlite3.Connection, token: str) -> None:
    """Invalidate a session token.

    # TODO Phase 2
    """
    raise NotImplementedError


def whoami(conn: sqlite3.Connection, token: str) -> dict:
    """Return user info for a session token.

    # TODO Phase 2
    """
    raise NotImplementedError


def change_password(
    conn: sqlite3.Connection,
    username: str,
    old_password: str,
    new_password: str,
) -> None:
    """Change a user's local password.

    # TODO Phase 2
    """
    raise NotImplementedError
