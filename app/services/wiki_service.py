"""Wiki service — article and image CRUD.

# TODO Phase 2 — implement
"""
from __future__ import annotations

import sqlite3


def list_articles(conn: sqlite3.Connection, *, role: str) -> list[dict]:
    """Return visible wiki articles for the given role.

    # TODO Phase 2
    """
    raise NotImplementedError


def get_article(conn: sqlite3.Connection, article_id: str, *, role: str) -> dict:
    """Return a single wiki article.

    # TODO Phase 2
    """
    raise NotImplementedError


def create_article(
    conn: sqlite3.Connection,
    *,
    title: str,
    body_md: str,
    pinned: bool,
    user: str,
    role: str,
) -> dict:
    """Create a new wiki article.

    # TODO Phase 2
    """
    raise NotImplementedError


def update_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    fields: dict,
    user: str,
    role: str,
) -> dict:
    """Update an existing wiki article.

    # TODO Phase 2
    """
    raise NotImplementedError


def delete_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    user: str,
    role: str,
) -> None:
    """Soft-delete a wiki article.

    # TODO Phase 2
    """
    raise NotImplementedError
