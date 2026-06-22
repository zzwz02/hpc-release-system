"""Release schedule repository — release_schedule table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def list_schedule(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all release schedule entries (ordered: non-empty dates first, then by date)."""
    rows = conn.execute(
        "SELECT * FROM release_schedule "
        "ORDER BY CASE WHEN branch_cut_at = '' THEN 1 ELSE 0 END, branch_cut_at, "
        "         CASE WHEN release_at = '' THEN 1 ELSE 0 END, release_at, created_at"
    )
    return [row_to_dict(row) for row in rows]


def get_schedule_entry(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    """Return a single schedule entry or None."""
    row = conn.execute(
        "SELECT * FROM release_schedule WHERE id = ?", (entry_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_schedule_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    version: str,
    branch_cut_at: str,
    release_at: str,
    note: str,
    created_at: str,
    created_by: str,
) -> None:
    """Insert a new schedule entry."""
    conn.execute(
        "INSERT INTO release_schedule(id, version, branch_cut_at, release_at, note, "
        "created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')",
        (entry_id, version, branch_cut_at, release_at, note, created_at, created_by),
    )


def update_schedule_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    version: str,
    branch_cut_at: str,
    release_at: str,
    note: str,
    updated_at: str,
    updated_by: str,
) -> None:
    """Update a schedule entry."""
    conn.execute(
        "UPDATE release_schedule SET version = ?, branch_cut_at = ?, release_at = ?, "
        "note = ?, updated_at = ?, updated_by = ? WHERE id = ?",
        (version, branch_cut_at, release_at, note, updated_at, updated_by, entry_id),
    )


def delete_schedule_entry(conn: sqlite3.Connection, entry_id: str) -> bool:
    """Delete a schedule entry. Returns True if it existed."""
    row = conn.execute(
        "SELECT version FROM release_schedule WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM release_schedule WHERE id = ?", (entry_id,))
    return True
