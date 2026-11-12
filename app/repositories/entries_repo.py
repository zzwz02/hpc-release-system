"""Signup entries and their scope-expansion requests.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict

#: Columns callers may change through update_entry().
MUTABLE_COLUMNS = frozenset(
    {
        "status",
        "base_image_id",
        "target_version",
        "planned_features",
        "actual_delivery",
        "known_issues",
        "locked_version_id",
        "excluded_tests",
        "qa_status",
        "qa_note",
        "deliverable_image",
        "deliverable_digest",
        "deliverable_commit",
        "result",
    }
)


def _entry(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["excluded_tests"] = loads_json(data.pop("excluded_tests_json", None), [])
    return data


def get_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _entry(row) if row else None


def find_entry(conn: sqlite3.Connection, release_id: str, app_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entries WHERE release_id = ? AND app_id = ?", (release_id, app_id)
    ).fetchone()
    return _entry(row) if row else None


def list_for_release(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    include_withdrawn: bool = False,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM entries WHERE release_id = ?"
    if not include_withdrawn:
        sql += " AND status = 'active'"
    return [_entry(row) for row in conn.execute(sql + " ORDER BY app_id", (release_id,))]


def list_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    return [
        _entry(row)
        for row in conn.execute(
            """
            SELECT entries.* FROM entries JOIN releases ON releases.id = entries.release_id
            WHERE entries.app_id = ? ORDER BY releases.created_at, releases.rowid
            """,
            (app_id,),
        )
    ]


def open_active_entries_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    """Active signups of *app_id* in cycles that are not published."""
    return [
        _entry(row)
        for row in conn.execute(
            """
            SELECT entries.* FROM entries JOIN releases ON releases.id = entries.release_id
            WHERE entries.app_id = ? AND entries.status = 'active'
              AND releases.released_locked = 0
            """,
            (app_id,),
        )
    ]


def by_digest(conn: sqlite3.Connection, digest: str) -> list[dict[str, Any]]:
    return [
        _entry(row)
        for row in conn.execute("SELECT * FROM entries WHERE deliverable_digest = ?", (digest,))
    ]


def insert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO entries(release_id, app_id, status, base_image_id, target_version,
                            planned_features, locked_version_id, excluded_tests_json,
                            created_at, created_by, updated_at, updated_by)
        VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry["release_id"],
            entry["app_id"],
            entry.get("base_image_id"),
            entry.get("target_version", ""),
            entry.get("planned_features", ""),
            entry.get("locked_version_id"),
            dumps_json(entry.get("excluded_tests", [])),
            entry["created_at"],
            entry["created_by"],
            entry["created_at"],
            entry["created_by"],
        ),
    )
    return int(cur.lastrowid)


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    fields: dict[str, Any],
    *,
    updated_at: str,
    updated_by: str,
) -> None:
    unknown = set(fields) - MUTABLE_COLUMNS
    if unknown:
        raise KeyError(f"unknown entry fields: {sorted(unknown)}")
    columns: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "excluded_tests":
            columns["excluded_tests_json"] = dumps_json(sorted(set(value)))
        else:
            columns[key] = value
    columns["updated_at"] = updated_at
    columns["updated_by"] = updated_by
    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.execute(f"UPDATE entries SET {assignments} WHERE id = ?", [*columns.values(), entry_id])


# ---------------------------------------------------------------------------
# Scope requests
# ---------------------------------------------------------------------------


def _scope(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["additions"] = loads_json(data.pop("additions_json", None), [])
    return data


def get_scope_request(conn: sqlite3.Connection, request_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scope_requests WHERE id = ?", (request_id,)).fetchone()
    return _scope(row) if row else None


def pending_scope_request(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM scope_requests WHERE entry_id = ? AND status = 'pending'"
        " ORDER BY id DESC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return _scope(row) if row else None


def last_rejected_request(
    conn: sqlite3.Connection,
    entry_id: int,
    base_version_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM scope_requests WHERE entry_id = ? AND base_version_id = ?"
        " AND status = 'rejected' ORDER BY id DESC LIMIT 1",
        (entry_id, base_version_id),
    ).fetchone()
    return _scope(row) if row else None


def list_scope_requests(
    conn: sqlite3.Connection,
    release_id: str,
) -> list[dict[str, Any]]:
    return [
        _scope(row)
        for row in conn.execute(
            """
            SELECT scope_requests.*, entries.app_id, entries.release_id
            FROM scope_requests JOIN entries ON entries.id = scope_requests.entry_id
            WHERE entries.release_id = ?
            ORDER BY scope_requests.id DESC
            """,
            (release_id,),
        )
    ]


def insert_scope_request(
    conn: sqlite3.Connection,
    *,
    entry_id: int,
    base_version_id: int,
    target_version_id: int,
    additions: list[str],
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO scope_requests(entry_id, base_version_id, target_version_id,
                                   additions_json, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (entry_id, base_version_id, target_version_id, dumps_json(additions), created_at),
    )
    return int(cur.lastrowid)


def close_scope_request(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    status: str,
    decided_by: str,
    decided_at: str,
    note: str = "",
) -> None:
    conn.execute(
        "UPDATE scope_requests SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?"
        " WHERE id = ?",
        (status, decided_by, decided_at, note, request_id),
    )
