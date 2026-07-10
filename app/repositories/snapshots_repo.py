"""Snapshots repository — snapshots table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
The data_json blob is treated as opaque TEXT; no internal keys are validated.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
) -> dict[str, Any] | None:
    """Return the parsed snapshot dict or None."""
    row = conn.execute(
        "SELECT data_json FROM snapshots WHERE release_id = ? AND app_id = ?",
        (release_id, app_id),
    ).fetchone()
    return loads_json(row["data_json"], None) if row else None


def snapshot_exists(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
) -> bool:
    """True if a snapshot row exists for this (release_id, app_id) pair."""
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE release_id = ? AND app_id = ?",
        (release_id, app_id),
    ).fetchone()
    return row is not None


def get_all_for_release(
    conn: sqlite3.Connection,
    release_id: str,
) -> dict[str, dict[str, Any]]:
    """Return {app_id: snapshot_dict} for all snapshots in a release."""
    return {
        row["app_id"]: loads_json(row["data_json"], {})
        for row in conn.execute(
            "SELECT app_id, data_json FROM snapshots WHERE release_id = ?",
            (release_id,),
        )
    }


def app_ids_in_release(conn: sqlite3.Connection, release_id: str) -> list[str]:
    """Return ordered list of app_ids present in a release's snapshots."""
    return [
        row["app_id"]
        for row in conn.execute(
            "SELECT app_id FROM snapshots WHERE release_id = ? ORDER BY app_id",
            (release_id,),
        )
    ]


def list_snapshots_for_release(
    conn: sqlite3.Connection,
    release_id: str,
) -> list[dict[str, Any]]:
    """Return snapshots joined with app metadata for a release.

    Each item has: release_id, app_id, data (parsed), git_url, git_branch.
    """
    rows = conn.execute(
        """
        SELECT s.release_id, s.app_id, s.data_json,
               a.git_url, a.git_branch
        FROM snapshots s
        JOIN apps a ON a.id = s.app_id
        WHERE s.release_id = ?
        ORDER BY s.app_id
        """,
        (release_id,),
    ).fetchall()
    return [
        {
            "release_id": r["release_id"],
            "app_id": r["app_id"],
            "data": loads_json(r["data_json"], {}),
            "git_url": r["git_url"],
            "git_branch": r["git_branch"],
        }
        for r in rows
    ]


def get_snapshots_for_app(
    conn: sqlite3.Connection,
    app_id: str,
) -> list[dict[str, Any]]:
    """Return the parsed snapshot dicts of *app_id* across all releases."""
    return [
        loads_json(row["data_json"], {})
        for row in conn.execute(
            "SELECT data_json FROM snapshots WHERE app_id = ?",
            (app_id,),
        )
    ]


def get_snapshots_with_decision(
    conn: sqlite3.Connection,
    app_id: str,
    *,
    exclude_locked: bool = True,
) -> list[dict[str, Any]]:
    """Return [{release_id, release_decision, released_locked}] for all releases containing app.

    Used for cross-release decision sync (plan §3.5 b+).
    """
    if exclude_locked:
        rows = conn.execute(
            """
            SELECT s.release_id, s.data_json, r.released_locked
            FROM snapshots s
            JOIN releases r ON r.id = s.release_id
            WHERE s.app_id = ? AND r.released_locked = 0
            ORDER BY r.created_at, r.rowid
            """,
            (app_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT s.release_id, s.data_json, r.released_locked
            FROM snapshots s
            JOIN releases r ON r.id = s.release_id
            WHERE s.app_id = ?
            ORDER BY r.created_at, r.rowid
            """,
            (app_id,),
        ).fetchall()
    result = []
    for row in rows:
        data = loads_json(row["data_json"], {})
        result.append(
            {
                "release_id": row["release_id"],
                "release_decision": data.get("release_decision", "release"),
                "released_locked": bool(row["released_locked"]),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def save_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    snapshot: dict[str, Any],
) -> None:
    """Upsert a snapshot row (no locked-check — caller must do that)."""
    conn.execute(
        """
        INSERT INTO snapshots(release_id, app_id, data_json)
        VALUES (?, ?, ?)
        ON CONFLICT(release_id, app_id) DO UPDATE SET data_json=excluded.data_json
        """,
        (release_id, app_id, dumps_json(snapshot)),
    )


def delete_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
) -> None:
    """Delete a single snapshot row."""
    conn.execute(
        "DELETE FROM snapshots WHERE release_id = ? AND app_id = ?",
        (release_id, app_id),
    )
