"""Apps repository — apps table CRUD and identity lookup.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_app(conn: sqlite3.Connection, app_id: str) -> dict | None:
    """Return the app row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def find_by_identity(
    conn: sqlite3.Connection,
    git_url: str,
    git_branch: str,
) -> dict | None:
    """Find an app by its (git_url, git_branch) natural key.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_app(conn: sqlite3.Connection, *, app_id: str, **fields) -> None:
    """Insert a new app row.

    # TODO Phase 1
    """
    raise NotImplementedError
