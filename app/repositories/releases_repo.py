"""Releases repository — releases table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def list_releases(conn: sqlite3.Connection) -> list[dict]:
    """Return all releases ordered by created_at DESC.

    # TODO Phase 1
    """
    raise NotImplementedError


def get_release(conn: sqlite3.Connection, release_id: str) -> dict | None:
    """Return a release row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_release(conn: sqlite3.Connection, *, release_id: str, **fields) -> None:
    """Insert a new release row.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_release(conn: sqlite3.Connection, release_id: str, **fields) -> None:
    """Update fields on an existing release.

    # TODO Phase 1
    """
    raise NotImplementedError


def delete_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete a release (cascades to snapshots, artifacts, qa_logs).

    # TODO Phase 1
    """
    raise NotImplementedError
