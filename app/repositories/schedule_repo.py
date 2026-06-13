"""Release schedule repository — release_schedule table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def list_schedule(conn: sqlite3.Connection) -> list[dict]:
    """Return all release schedule entries.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_schedule_entry(conn: sqlite3.Connection, *, entry_id: str, **fields) -> None:
    """Insert a new schedule entry.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_schedule_entry(conn: sqlite3.Connection, entry_id: str, **fields) -> None:
    """Update a schedule entry.

    # TODO Phase 1
    """
    raise NotImplementedError


def delete_schedule_entry(conn: sqlite3.Connection, entry_id: str) -> None:
    """Delete a schedule entry.

    # TODO Phase 1
    """
    raise NotImplementedError
