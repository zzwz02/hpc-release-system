"""Releases repository — monthly cycles plus their environment and base images.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row)
    data["released_locked"] = bool(data.get("released_locked"))
    return data


def get_release_row(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
    return _row(row) if row else None


def list_release_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All cycles, oldest first."""
    return [_row(row) for row in conn.execute("SELECT * FROM releases ORDER BY created_at, rowid")]


def name_exists(conn: sqlite3.Connection, name: str, *, exclude_id: str = "") -> bool:
    row = conn.execute(
        "SELECT 1 FROM releases WHERE name = ? AND id <> ?", (name, exclude_id)
    ).fetchone()
    return row is not None


def insert_release(conn: sqlite3.Connection, release: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO releases(id, name, signup_deadline, qa_deadline, release_date,
                             released_locked, released_locked_at, released_locked_by,
                             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, 0, '', '', ?, ?)
        """,
        (
            release["id"],
            release["name"],
            release["signup_deadline"],
            release["qa_deadline"],
            release["release_date"],
            release["created_at"],
            release.get("created_by", ""),
        ),
    )


def update_settings(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    name: str,
    signup_deadline: str,
    qa_deadline: str,
    release_date: str,
) -> None:
    conn.execute(
        """
        UPDATE releases SET name = ?, signup_deadline = ?, qa_deadline = ?, release_date = ?
        WHERE id = ?
        """,
        (name, signup_deadline, qa_deadline, release_date, release_id),
    )


def lock_release(conn: sqlite3.Connection, release_id: str, *, locked_at: str, locked_by: str) -> None:
    conn.execute(
        "UPDATE releases SET released_locked = 1, released_locked_at = ?,"
        " released_locked_by = ? WHERE id = ?",
        (locked_at, locked_by, release_id),
    )


def unlock_release(conn: sqlite3.Connection, release_id: str) -> None:
    conn.execute(
        "UPDATE releases SET released_locked = 0, released_locked_at = '',"
        " released_locked_by = '' WHERE id = ?",
        (release_id,),
    )


def delete_release(conn: sqlite3.Connection, release_id: str) -> None:
    conn.execute("DELETE FROM releases WHERE id = ?", (release_id,))


# ---------------------------------------------------------------------------
# Environment rows
# ---------------------------------------------------------------------------


def list_environment(conn: sqlite3.Connection, release_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT id, component, version, note, sort FROM cycle_environment"
            " WHERE release_id = ? ORDER BY sort, id",
            (release_id,),
        )
    ]


def replace_environment(
    conn: sqlite3.Connection,
    release_id: str,
    rows: list[dict[str, str]],
) -> None:
    conn.execute("DELETE FROM cycle_environment WHERE release_id = ?", (release_id,))
    for index, row in enumerate(rows):
        conn.execute(
            "INSERT INTO cycle_environment(release_id, component, version, note, sort)"
            " VALUES (?, ?, ?, ?, ?)",
            (release_id, row["component"], row.get("version", ""), row.get("note", ""), index),
        )


# ---------------------------------------------------------------------------
# Base images
# ---------------------------------------------------------------------------


def list_base_images(conn: sqlite3.Connection, release_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT id, release_id, name, image, digest, note FROM base_images"
            " WHERE release_id = ? ORDER BY id",
            (release_id,),
        )
    ]


def get_base_image(conn: sqlite3.Connection, image_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM base_images WHERE id = ?", (image_id,)).fetchone()
    return row_to_dict(row) if row else None


def insert_base_image(conn: sqlite3.Connection, release_id: str, image: dict[str, str]) -> int:
    cur = conn.execute(
        "INSERT INTO base_images(release_id, name, image, digest, note) VALUES (?, ?, ?, ?, ?)",
        (release_id, image["name"], image.get("image", ""), image.get("digest", ""), image.get("note", "")),
    )
    return int(cur.lastrowid)


def update_base_image(conn: sqlite3.Connection, image_id: int, image: dict[str, str]) -> None:
    conn.execute(
        "UPDATE base_images SET name = ?, image = ?, digest = ?, note = ? WHERE id = ?",
        (image["name"], image.get("image", ""), image.get("digest", ""), image.get("note", ""), image_id),
    )


def delete_base_image(conn: sqlite3.Connection, image_id: int) -> None:
    conn.execute("DELETE FROM base_images WHERE id = ?", (image_id,))


def base_images_by_digest(conn: sqlite3.Connection, digest: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM base_images WHERE digest = ?", (digest,))
    ]
