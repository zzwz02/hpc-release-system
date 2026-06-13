"""Apps repository — apps table CRUD and identity lookup.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict

# ---------------------------------------------------------------------------
# Row shaping — mirror core.py:row_to_app
# ---------------------------------------------------------------------------

def _row_to_app(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["aliases"] = loads_json(data.pop("aliases_json", None), [])
    return data


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_app(conn: sqlite3.Connection, app_id: str) -> dict[str, Any] | None:
    """Return the app row (with aliases list) or None."""
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    return _row_to_app(row) if row else None


def list_apps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all apps ordered by id."""
    return [_row_to_app(row) for row in conn.execute("SELECT * FROM apps ORDER BY id")]


def find_by_identity(
    conn: sqlite3.Connection,
    git_url: str,
    git_branch: str,
) -> dict[str, Any] | None:
    """Find an app by its (git_url, git_branch) natural key.

    Both sides must be the normalized full URL — use domain/identity.py
    normalize_git_url before calling this.
    """
    row = conn.execute(
        "SELECT * FROM apps WHERE git_url = ? AND git_branch = ?",
        (git_url, git_branch),
    ).fetchone()
    return _row_to_app(row) if row else None


def all_app_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of all existing app ids (for ID collision checks)."""
    return {row["id"] for row in conn.execute("SELECT id FROM apps")}


def locked_releases_for_app(conn: sqlite3.Connection, app_id: str) -> list[str]:
    """Return release names where this app appears and the release is locked."""
    return [
        row["name"]
        for row in conn.execute(
            """
            SELECT releases.name
            FROM releases
            JOIN snapshots ON snapshots.release_id = releases.id
            WHERE snapshots.app_id = ? AND releases.released_locked = 1
            ORDER BY releases.created_at
            """,
            (app_id,),
        )
    ]


def affected_release_ids_for_app(conn: sqlite3.Connection, app_id: str) -> list[str]:
    """Return release_ids where this app has a snapshot (for artifact cleanup)."""
    return [
        row["release_id"]
        for row in conn.execute("SELECT release_id FROM snapshots WHERE app_id = ?", (app_id,))
    ]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def save_app(conn: sqlite3.Connection, app: dict[str, Any]) -> None:
    """Upsert an app row — mirrors core.py:save_app."""
    conn.execute(
        """
        INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          git_url=excluded.git_url,
          git_branch=excluded.git_branch,
          aliases_json=excluded.aliases_json,
          created_by=excluded.created_by
        """,
        (
            app["id"],
            app.get("git_url", ""),
            app.get("git_branch", ""),
            dumps_json(sorted(set(app.get("aliases", [])))),
            app.get("created_by", "import"),
            app.get("created_at", ""),
        ),
    )


def delete_app(conn: sqlite3.Connection, app_id: str) -> None:
    """Delete an app row (business preconditions enforced by caller)."""
    conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))


def delete_draft_artifacts_for_releases(
    conn: sqlite3.Connection,
    release_ids: list[str],
) -> None:
    """Delete draft (non-final) artifacts for a list of releases."""
    for rid in release_ids:
        conn.execute("DELETE FROM artifacts WHERE release_id = ? AND final = 0", (rid,))
