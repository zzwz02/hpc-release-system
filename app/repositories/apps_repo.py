"""Apps repository — apps table CRUD and identity lookup.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.cicd_config import CICD_APP_CONFIG_FIELDS
from app.repositories.base import dumps_json, loads_json, row_to_dict

# ---------------------------------------------------------------------------
# Row shaping — mirror core.py:row_to_app
# ---------------------------------------------------------------------------

_JSON_COLUMNS = {
    "aliases_json": ("aliases", list),
    "owners_json": ("owners", list),
    "doc_json": ("doc", dict),
    "community_json": ("community", dict),
    "sanity_json": ("sanity", dict),
    "test_docs_json": ("test_docs", list),
}

#: Optional Owner profile fields stored on the App (key -> column).
PROFILE_COLUMNS = {
    "official_name": "official_name",
    "type": "app_type",
    "official_url": "official_url",
    "description": "description",
    "doc_target": "doc_target",
    "owners": "owners_json",
    "doc": "doc_json",
    "community": "community_json",
    "sanity": "sanity_json",
    "test_docs": "test_docs_json",
}


def _row_to_app(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    for column, (key, kind) in _JSON_COLUMNS.items():
        data[key] = loads_json(data.pop(column, None), kind())
    data["type"] = data.pop("app_type", "")
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

    This exact lookup does not normalize identities. Callers comparing a
    stored short path with a derived full URL must use app.identity helpers.
    """
    row = conn.execute(
        "SELECT * FROM apps WHERE git_url = ? AND git_branch = ?",
        (git_url, git_branch),
    ).fetchone()
    return _row_to_app(row) if row else None


def all_app_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of all existing app ids (for ID collision checks)."""
    return {row["id"] for row in conn.execute("SELECT id FROM apps")}


def owned_app_ids(conn: sqlite3.Connection, username: str) -> set[str]:
    return {
        row["id"]
        for row in conn.execute(
            "SELECT apps.id FROM apps, json_each(apps.owners_json) WHERE json_each.value = ?",
            (username,),
        )
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_app(conn: sqlite3.Connection, app: dict[str, Any]) -> None:
    """Insert a new App row (identity, CICD config and initial profile)."""
    conn.execute(
        """
        INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at,
                         cicd_status, official_name, owners_json, doc_target, app_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app["id"],
            app.get("git_url", ""),
            app.get("git_branch", ""),
            dumps_json(sorted(set(app.get("aliases", [])))),
            app.get("created_by", ""),
            app.get("created_at", ""),
            app.get("cicd_status", "running"),
            app.get("official_name", ""),
            dumps_json(sorted(set(app.get("owners", [])))),
            app.get("doc_target", "manual"),
            app.get("type", ""),
        ),
    )


def update_identity(conn: sqlite3.Connection, app_id: str, *, git_url: str, git_branch: str) -> None:
    conn.execute(
        "UPDATE apps SET git_url = ?, git_branch = ? WHERE id = ?",
        (git_url, git_branch, app_id),
    )


def update_profile(conn: sqlite3.Connection, app_id: str, fields: dict[str, Any]) -> None:
    """Persist profile fields (keys of PROFILE_COLUMNS)."""
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        column = PROFILE_COLUMNS[key]
        updates[column] = dumps_json(value) if column.endswith("_json") else str(value or "")
    if not updates:
        return
    assignments = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(f"UPDATE apps SET {assignments} WHERE id = ?", [*updates.values(), app_id])


def set_cicd_status(conn: sqlite3.Connection, app_id: str, status: str) -> None:
    conn.execute("UPDATE apps SET cicd_status = ? WHERE id = ?", (status, app_id))


def update_sync_state(
    conn: sqlite3.Connection,
    app_id: str,
    *,
    checked_at: str,
    error: str,
    head_commit: str | None = None,
) -> None:
    if head_commit is None:
        conn.execute(
            "UPDATE apps SET sync_checked_at = ?, sync_error = ? WHERE id = ?",
            (checked_at, error, app_id),
        )
    else:
        conn.execute(
            "UPDATE apps SET sync_checked_at = ?, sync_error = ?, sync_head_commit = ? WHERE id = ?",
            (checked_at, error, head_commit, app_id),
        )


def delete_app(conn: sqlite3.Connection, app_id: str) -> None:
    """Delete an app row (business preconditions enforced by caller)."""
    conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))


def update_cicd_config(
    conn: sqlite3.Connection,
    app_id: str,
    fields: dict[str, Any],
) -> None:
    """Persist already-normalized CICD config columns directly on apps."""
    updates = {
        key: str(value)
        for key, value in (fields or {}).items()
        if key in CICD_APP_CONFIG_FIELDS
    }
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE apps SET {assignments} WHERE id = ?",
        [*updates.values(), app_id],
    )
