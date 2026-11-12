"""app_info versions and declared component dependencies.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.repositories.base import row_to_dict


def _version(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["raw"] = json.loads(data.pop("raw_json"))
    return data


def get_version(conn: sqlite3.Connection, version_id: int | None) -> dict[str, Any] | None:
    if version_id is None:
        return None
    row = conn.execute("SELECT * FROM app_info_versions WHERE id = ?", (version_id,)).fetchone()
    return _version(row) if row else None


def latest_version(conn: sqlite3.Connection, app_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM app_info_versions WHERE app_id = ? ORDER BY id DESC LIMIT 1",
        (app_id,),
    ).fetchone()
    return _version(row) if row else None


def latest_version_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["app_id"]: int(row["id"])
        for row in conn.execute(
            "SELECT app_id, MAX(id) AS id FROM app_info_versions GROUP BY app_id"
        )
    }


def image_lookup_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Historical image naming metadata; omit the potentially large test payloads."""
    return [
        {"app_id": row["app_id"], "raw": json.loads(row["raw_json"])}
        for row in conn.execute(
            "SELECT app_id, json_remove(raw_json, '$.app_test') AS raw_json FROM app_info_versions"
        )
    ]


def list_versions(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    """Version headers (no content), newest first."""
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT id, app_id, commit_id, content_hash, source, fetched_at, fetched_by"
            " FROM app_info_versions WHERE app_id = ? ORDER BY id DESC",
            (app_id,),
        )
    ]


def insert_version(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    commit_id: str,
    content_hash: str,
    raw_json: str,
    source: str,
    fetched_at: str,
    fetched_by: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO app_info_versions(app_id, commit_id, content_hash, raw_json,
                                      source, fetched_at, fetched_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, commit_id, content_hash, raw_json, source, fetched_at, fetched_by),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def replace_dependencies(conn: sqlite3.Connection, app_id: str, components: list[str]) -> None:
    conn.execute("DELETE FROM app_dependencies WHERE app_id = ?", (app_id,))
    for component in components:
        conn.execute(
            "INSERT OR IGNORE INTO app_dependencies(app_id, component) VALUES (?, ?)",
            (app_id, component),
        )


def dependencies_by_app(conn: sqlite3.Connection) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in conn.execute("SELECT app_id, component FROM app_dependencies ORDER BY component"):
        result.setdefault(row["app_id"], []).append(row["component"])
    return result
