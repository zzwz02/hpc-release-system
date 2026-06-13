"""Releases repository — releases table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_release_row(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    """Return the raw release row (no snapshots attached) or None."""
    row = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
    if not row:
        return None
    data = row_to_dict(row)
    data["released_locked"] = bool(data.get("released_locked"))
    return data


def list_release_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all release rows ordered by created_at, rowid (no snapshots)."""
    result = []
    for row in conn.execute("SELECT * FROM releases ORDER BY created_at, rowid"):
        data = row_to_dict(row)
        data["released_locked"] = bool(data.get("released_locked"))
        result.append(data)
    return result


def release_is_locked(conn: sqlite3.Connection, release_id: str) -> bool:
    """True if the release is locked (fast check, no join)."""
    row = conn.execute(
        "SELECT released_locked FROM releases WHERE id = ?", (release_id,)
    ).fetchone()
    return bool(row and row["released_locked"])


def previous_release_id(conn: sqlite3.Connection, release_id: str) -> str | None:
    """Return the id of the release that immediately precedes *release_id*, or None."""
    releases = list_release_rows(conn)
    for i, rel in enumerate(releases):
        if rel["id"] == release_id and i > 0:
            return releases[i - 1]["id"]
    return None


def future_unlocked_release_ids(
    conn: sqlite3.Connection,
    from_release_id: str,
) -> list[str]:
    """Return ids of releases from *from_release_id* onward that are not locked.

    Used for forward-propagating new app snapshots (mirrors core.py
    _future_unlocked_release_ids).
    """
    releases = list_release_rows(conn)
    found = False
    result = []
    for rel in releases:
        if rel["id"] == from_release_id:
            found = True
        if found and not rel["released_locked"]:
            result.append(rel["id"])
    return result


def all_unlocked_release_ids(conn: sqlite3.Connection) -> list[str]:
    """Return ids of all unlocked releases (for cross-release decision sync)."""
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM releases WHERE released_locked = 0 ORDER BY created_at, rowid"
        )
    ]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def save_release(conn: sqlite3.Connection, release: dict[str, Any]) -> None:
    """Upsert a release row — mirrors core.py:save_release (no deadline normaliz.)."""
    conn.execute(
        """
        INSERT INTO releases(id, name, maca_version, app_freeze_deadline, doc_deadline,
                             released_locked, released_locked_at, released_locked_by,
                             created_at, source, cloned_from)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          maca_version=excluded.maca_version,
          app_freeze_deadline=excluded.app_freeze_deadline,
          doc_deadline=excluded.doc_deadline,
          released_locked=excluded.released_locked,
          released_locked_at=excluded.released_locked_at,
          released_locked_by=excluded.released_locked_by,
          created_at=excluded.created_at,
          source=excluded.source,
          cloned_from=excluded.cloned_from
        """,
        (
            release["id"],
            release["name"],
            release.get("maca_version", ""),
            release.get("app_freeze_deadline", ""),
            release.get("doc_deadline", ""),
            int(release.get("released_locked", 0)),
            release.get("released_locked_at", ""),
            release.get("released_locked_by", ""),
            release.get("created_at", ""),
            release.get("source", "manual"),
            release.get("cloned_from", ""),
        ),
    )


def update_deadlines(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    name: str,
    app_freeze_deadline: str,
    doc_deadline: str,
) -> None:
    """Update name + deadline columns on a release."""
    conn.execute(
        "UPDATE releases SET name = ?, app_freeze_deadline = ?, doc_deadline = ? WHERE id = ?",
        (name, app_freeze_deadline, doc_deadline, release_id),
    )


def lock_release(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    locked_at: str,
    locked_by: str,
) -> None:
    """Set released_locked = 1 on a release."""
    conn.execute(
        "UPDATE releases SET released_locked = 1, released_locked_at = ?,"
        " released_locked_by = ? WHERE id = ?",
        (locked_at, locked_by, release_id),
    )


def unlock_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Clear released_locked on a release (admin safety valve)."""
    conn.execute(
        "UPDATE releases SET released_locked = 0, released_locked_at = '',"
        " released_locked_by = '' WHERE id = ?",
        (release_id,),
    )


def delete_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete a release (cascades to snapshots, artifacts, qa_logs via FK)."""
    conn.execute("DELETE FROM releases WHERE id = ?", (release_id,))
