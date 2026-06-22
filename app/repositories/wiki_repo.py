"""Wiki repository — wiki_articles and wiki_images table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Article shaping
# ---------------------------------------------------------------------------

def _row_to_article(row: sqlite3.Row) -> dict[str, Any]:
    article = row_to_dict(row)
    article["pinned"] = bool(article.get("pinned"))
    article["deleted"] = bool(article.get("deleted"))
    return article


# ---------------------------------------------------------------------------
# Article reads
# ---------------------------------------------------------------------------

def list_articles(
    conn: sqlite3.Connection,
    *,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    """Return visible wiki articles ordered by pinned DESC, updated_at DESC."""
    where = "" if include_deleted else "WHERE deleted = 0"
    rows = conn.execute(
        f"""
        SELECT id, title, body_md, pinned, created_by, created_at,
               updated_by, updated_at, deleted
        FROM wiki_articles
        {where}
        ORDER BY pinned DESC, updated_at DESC, created_at DESC
        """
    ).fetchall()
    return [_row_to_article(row) for row in rows]


def get_article(conn: sqlite3.Connection, article_id: str) -> dict[str, Any] | None:
    """Return a non-deleted wiki article or None."""
    row = conn.execute(
        """
        SELECT id, title, body_md, pinned, created_by, created_at,
               updated_by, updated_at, deleted
        FROM wiki_articles
        WHERE id = ? AND deleted = 0
        """,
        (article_id,),
    ).fetchone()
    return _row_to_article(row) if row else None


# ---------------------------------------------------------------------------
# Article writes
# ---------------------------------------------------------------------------

def insert_article(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    title: str,
    body_md: str,
    pinned: int,
    created_by: str,
    created_at: str,
) -> None:
    """Insert a new wiki article."""
    conn.execute(
        "INSERT INTO wiki_articles(id, title, body_md, pinned, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (article_id, title, body_md, pinned, created_by, created_at),
    )


def update_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    title: str,
    body_md: str,
    pinned: int,
    updated_by: str,
    updated_at: str,
) -> None:
    """Update a wiki article's content."""
    conn.execute(
        "UPDATE wiki_articles SET title = ?, body_md = ?, pinned = ?, "
        "updated_by = ?, updated_at = ? WHERE id = ?",
        (title, body_md, pinned, updated_by, updated_at, article_id),
    )


def soft_delete_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    deleted_by: str,
    deleted_at: str,
) -> None:
    """Soft-delete a wiki article."""
    conn.execute(
        "UPDATE wiki_articles SET deleted = 1, deleted_by = ?, deleted_at = ? WHERE id = ?",
        (deleted_by, deleted_at, article_id),
    )


# ---------------------------------------------------------------------------
# Image reads/writes
# ---------------------------------------------------------------------------

def get_image(conn: sqlite3.Connection, image_id: str) -> dict[str, Any] | None:
    """Return a wiki image row (including blob) or None."""
    row = conn.execute(
        "SELECT * FROM wiki_images WHERE id = ?", (image_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def insert_image(
    conn: sqlite3.Connection,
    *,
    image_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    uploaded_by: str,
    uploaded_at: str,
) -> None:
    """Insert a new wiki image."""
    conn.execute(
        "INSERT INTO wiki_images(id, filename, content_type, content, uploaded_by, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (image_id, filename, content_type, content, uploaded_by, uploaded_at),
    )
