"""Artifacts repository — artifacts table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
) -> dict[str, Any] | None:
    """Return an artifact row or None."""
    row = conn.execute(
        "SELECT * FROM artifacts WHERE release_id = ? AND kind = ?",
        (release_id, kind),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_artifacts(
    conn: sqlite3.Connection,
    release_id: str,
) -> list[dict[str, Any]]:
    """Return all artifact rows for a release."""
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM artifacts WHERE release_id = ? ORDER BY kind",
            (release_id,),
        )
    ]


def has_final_artifacts(conn: sqlite3.Connection, release_id: str) -> bool:
    """True if any final artifact exists for this release."""
    row = conn.execute(
        "SELECT 1 FROM artifacts WHERE release_id = ? AND final = 1 LIMIT 1",
        (release_id,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
    *,
    name: str,
    content: str,
    generated_at: str,
    final: int = 0,
) -> None:
    """Insert or replace an artifact row."""
    conn.execute(
        """
        INSERT INTO artifacts(release_id, kind, name, content, final, generated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id, kind) DO UPDATE SET
          name=excluded.name,
          content=excluded.content,
          final=excluded.final,
          generated_at=excluded.generated_at
        """,
        (release_id, kind, name, content, final, generated_at),
    )


def delete_draft_artifacts(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete non-final (draft) artifacts for a release."""
    conn.execute("DELETE FROM artifacts WHERE release_id = ? AND final = 0", (release_id,))


def delete_final_artifacts(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete final artifacts for a release."""
    conn.execute("DELETE FROM artifacts WHERE release_id = ? AND final = 1", (release_id,))


def delete_all_artifacts(conn: sqlite3.Connection, release_id: str) -> None:
    """Delete all artifacts for a release."""
    conn.execute("DELETE FROM artifacts WHERE release_id = ?", (release_id,))
