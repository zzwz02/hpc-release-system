"""Official (released) versions of Apps.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict


def current_by_app(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Each App's newest non-withdrawn official version, with its cycle name."""
    rows = conn.execute(
        """
        SELECT o.*, r.name AS release_name
        FROM official_versions o JOIN releases r ON r.id = o.release_id
        WHERE o.withdrawn = 0
          AND o.id = (SELECT MAX(id) FROM official_versions
                      WHERE app_id = o.app_id AND withdrawn = 0)
        """
    ).fetchall()
    return {row["app_id"]: row_to_dict(row) for row in rows}


def history_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT o.*, r.name AS release_name
            FROM official_versions o JOIN releases r ON r.id = o.release_id
            WHERE o.app_id = ? ORDER BY o.id DESC
            """,
            (app_id,),
        )
    ]


def by_digest(conn: sqlite3.Connection, digest: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM official_versions WHERE digest = ?", (digest,))
    ]


def insert_official(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO official_versions(app_id, release_id, entry_id, version,
                                      app_info_version_id, image, digest, released_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["app_id"],
            record["release_id"],
            record["entry_id"],
            record.get("version", ""),
            record.get("app_info_version_id"),
            record.get("image", ""),
            record.get("digest", ""),
            record["released_at"],
        ),
    )


def withdraw_for_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Mark a cycle's official versions withdrawn (cycle unlocked for correction)."""
    conn.execute(
        "UPDATE official_versions SET withdrawn = 1 WHERE release_id = ? AND withdrawn = 0",
        (release_id,),
    )
