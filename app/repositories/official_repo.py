"""Official (released) versions of Apps.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.cycles import ENTRY_ACTIVE, RESULT_RELEASED
from app.repositories.base import dumps_json, loads_json, row_to_dict


def _official(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data['images'] = loads_json(data.pop('images_json', None), [])
    return data


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
    return {row["app_id"]: _official(row) for row in rows}


def history_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    """Include publications retained only in entries by the monthly migration.

    A fallback row has no official-version ID. Do not persist it: historical
    publications must not change the catalog's current official version.
    Explicit official records (including withdrawals) take precedence.
    """
    return [
        _official(row)
        for row in conn.execute(
            """
            WITH history AS (
                SELECT o.id,o.app_id,o.release_id,o.entry_id,o.version,o.app_info_version_id,
                       o.image,o.digest,o.released_at,o.withdrawn,o.images_json
                FROM official_versions o WHERE o.app_id = ?
                UNION ALL
                SELECT NULL AS id, e.app_id, e.release_id, e.id AS entry_id,
                       e.target_version AS version, e.locked_version_id AS app_info_version_id,
                       e.deliverable_image AS image, e.deliverable_digest AS digest,
                       COALESCE(NULLIF(r.released_locked_at, ''), e.updated_at) AS released_at,
                       0 AS withdrawn, e.deliverable_images_json AS images_json
                FROM entries e JOIN releases r ON r.id = e.release_id
                WHERE e.app_id = ? AND e.status = ? AND e.result = ?
                  AND r.released_locked = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM official_versions o
                      WHERE o.app_id = e.app_id AND o.release_id = e.release_id
                  )
            )
            SELECT h.*, r.name AS release_name
            FROM history h JOIN releases r ON r.id = h.release_id
            ORDER BY h.released_at DESC, h.id DESC, h.entry_id DESC
            """,
            (app_id, app_id, ENTRY_ACTIVE, RESULT_RELEASED),
        )
    ]


def by_digest(conn: sqlite3.Connection, digest: str) -> list[dict[str, Any]]:
    return [
        _official(row)
        for row in conn.execute("SELECT * FROM official_versions WHERE digest = ? OR EXISTS "
                                "(SELECT 1 FROM json_each(images_json) WHERE json_extract(value, '$.digest')=?)", (digest, digest))
    ]


def insert_official(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO official_versions(app_id, release_id, entry_id, version,
                                      app_info_version_id, image, digest, released_at, images_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            dumps_json(record.get('images') or []),
        ),
    )


def withdraw_for_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Mark a cycle's official versions withdrawn (cycle unlocked for correction)."""
    conn.execute(
        "UPDATE official_versions SET withdrawn = 1 WHERE release_id = ? AND withdrawn = 0",
        (release_id,),
    )
