"""Snapshots repository — snapshots table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
) -> dict | None:
    """Return the snapshot data_json (parsed) or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def upsert_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    data: dict,
) -> None:
    """Insert or replace a snapshot row.

    # TODO Phase 1
    """
    raise NotImplementedError


def list_snapshots_for_release(
    conn: sqlite3.Connection,
    release_id: str,
) -> list[dict]:
    """Return all snapshots for a release with app metadata joined.

    # TODO Phase 1
    """
    raise NotImplementedError
