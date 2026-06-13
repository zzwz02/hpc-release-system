"""Admin service — user/role management and system maintenance (Ruling-C).

Admin is confined to: users, db backup/clear, global app delete.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def list_users(conn: sqlite3.Connection) -> list[dict]:
    """Return all users.

    # TODO Phase 2
    """
    raise NotImplementedError


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str,
    actor: str,
) -> dict:
    """Create a new local user.

    # TODO Phase 2
    """
    raise NotImplementedError


def update_user_role(
    conn: sqlite3.Connection,
    username: str,
    *,
    role: str,
    actor: str,
) -> None:
    """Update a user's role.

    # TODO Phase 2
    """
    raise NotImplementedError


def delete_user(conn: sqlite3.Connection, username: str, *, actor: str) -> None:
    """Delete a user.

    # TODO Phase 2
    """
    raise NotImplementedError


def clear_business_data(conn: sqlite3.Connection, *, actor: str) -> None:
    """Clear all business data (releases, apps, CICD, etc.) for dev reset.

    Also clears CICD tables to avoid orphans (plan C6).

    # TODO Phase 2
    """
    raise NotImplementedError
