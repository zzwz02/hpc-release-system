"""Official (released) versions of Apps.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.cycles import ENTRY_ACTIVE, RESULT_RELEASED
from app.repositories.base import dumps_json, loads_json, row_to_dict
from app.repositories.releases_repo import cycle_order_sql


def _official(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data['images'] = loads_json(data.pop('images_json', None), [])
    try:
        raw_manifest = data.pop('test_manifest_json', None)
        data['test_manifest'] = loads_json(raw_manifest, None)
        if raw_manifest is not None and data['test_manifest'] is None:
            raise ValueError("non-null manifest is empty")
    except ValueError as exc:
        raise ValueError("发布测试清单 JSON 损坏，无法读取已封存记录") from exc
    return data


# Migrated publications retained only in entries remain readable without creating new records.
_PUBLICATIONS_CTE = """
WITH publications AS (
    SELECT o.id,o.app_id,o.release_id,o.entry_id,o.version,o.app_info_version_id,
           o.image,o.digest,o.released_at,o.images_json,o.test_manifest_json
    FROM official_versions o JOIN releases r ON r.id=o.release_id
    WHERE r.released_locked=1
    UNION ALL
    SELECT NULL AS id,e.app_id,e.release_id,e.id AS entry_id,
           e.target_version AS version,e.locked_version_id AS app_info_version_id,
           e.deliverable_image AS image,e.deliverable_digest AS digest,
           COALESCE(NULLIF(r.released_locked_at,''),e.updated_at) AS released_at,
           e.deliverable_images_json AS images_json,NULL AS test_manifest_json
    FROM entries e JOIN releases r ON r.id=e.release_id
    WHERE e.status=? AND e.result=? AND r.released_locked=1
      AND NOT EXISTS (SELECT 1 FROM official_versions o WHERE o.app_id=e.app_id AND o.release_id=e.release_id)
)
"""
_PUBLICATION_PARAMS = (ENTRY_ACTIVE, RESULT_RELEASED)


def current_by_app(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Each App's latest successful cycle, regardless of when a correction was written."""
    rows = conn.execute(
        _PUBLICATIONS_CTE + f"""
        , ranked AS (
            SELECT p.*,r.name AS release_name,
                   ROW_NUMBER() OVER (PARTITION BY p.app_id ORDER BY {cycle_order_sql('r', newest_first=True)}) AS cycle_rank
            FROM publications p JOIN releases r ON r.id=p.release_id
        )
        SELECT * FROM ranked WHERE cycle_rank=1
        """, _PUBLICATION_PARAMS,
    )
    result = {}
    for row in rows:
        record = _official(row)
        record.pop("cycle_rank")
        result[record["app_id"]] = record
    return result


def history_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    """One final record per published cycle, newest cycle first; reads never create history."""
    return [_official(row) for row in conn.execute(
        _PUBLICATIONS_CTE + "SELECT p.*,r.name AS release_name FROM publications p "
        f"JOIN releases r ON r.id=p.release_id WHERE p.app_id=? ORDER BY {cycle_order_sql('r', newest_first=True)}",
        (*_PUBLICATION_PARAMS, app_id),
    )]


def by_digest(conn: sqlite3.Connection, digest: str) -> list[dict[str, Any]]:
    digest = digest.strip().lower()
    bare = digest.removeprefix("sha256:")
    return [_official(row) for row in conn.execute(
        _PUBLICATIONS_CTE + "SELECT p.*,r.name AS release_name FROM publications p JOIN releases r ON r.id=p.release_id "
        "WHERE lower(trim(p.digest)) IN (?,?) OR lower(trim(p.image)) LIKE ? "
        "OR EXISTS (SELECT 1 FROM json_each(p.images_json) WHERE lower(trim(json_extract(value,'$.digest'))) IN (?,?) "
        "OR lower(trim(json_extract(value,'$.reference'))) LIKE ? OR lower(trim(json_extract(value,'$.image'))) LIKE ?) "
        f"ORDER BY {cycle_order_sql('r', newest_first=True)},p.app_id",
        (*_PUBLICATION_PARAMS, digest, bare, f"%@{digest}", digest, bare, f"%@{digest}", f"%@{digest}"),
    )]


def current_for_release(conn: sqlite3.Connection, release_id: str) -> dict[str, dict[str, Any]]:
    """The single final publication of each App in one locked cycle."""
    return {row["app_id"]: _official(row) for row in conn.execute(
        _PUBLICATIONS_CTE + "SELECT p.*,r.name AS release_name FROM publications p "
        "JOIN releases r ON r.id=p.release_id WHERE p.release_id=?", (*_PUBLICATION_PARAMS, release_id),
    )}


def insert_official(conn: sqlite3.Connection, record: dict[str, Any]) -> int:
    cur = conn.execute(
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
    official_id = int(cur.lastrowid)
    if record.get("test_manifest") is not None:
        manifest = {**record["test_manifest"], "official_version_id": official_id}
        conn.execute("UPDATE official_versions SET test_manifest_json=? WHERE id=?",
                     (dumps_json(manifest), official_id))
    return official_id


def delete_for_release(conn: sqlite3.Connection, release_id: str) -> None:
    """Unlocking deletes the cycle's final publications and their manifests."""
    conn.execute("DELETE FROM official_versions WHERE release_id=?", (release_id,))
