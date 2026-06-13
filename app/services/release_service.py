"""Release lifecycle service — create, clone, lock, update deadlines, delete.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def create_release(conn: sqlite3.Connection, *, name: str, user: str, **kwargs) -> dict:
    """Create a new release.

    # TODO Phase 2
    """
    raise NotImplementedError


def clone_release(
    conn: sqlite3.Connection,
    source_release_id: str,
    *,
    name: str,
    user: str,
) -> dict:
    """Clone an existing release (snapshot copy).

    # TODO Phase 2
    """
    raise NotImplementedError


def lock_release(conn: sqlite3.Connection, release_id: str, *, user: str) -> dict:
    """Lock (freeze) a release.

    # TODO Phase 2
    """
    raise NotImplementedError


def update_deadlines(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    app_freeze_deadline: str | None,
    doc_deadline: str | None,
    user: str,
) -> dict:
    """Update a release's deadline fields.

    # TODO Phase 2
    """
    raise NotImplementedError


def delete_release(conn: sqlite3.Connection, release_id: str, *, user: str) -> None:
    """Delete a release.

    # TODO Phase 2
    """
    raise NotImplementedError
