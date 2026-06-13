"""Wiki repository — wiki_articles and wiki_images table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def list_articles(conn: sqlite3.Connection, *, include_deleted: bool = False) -> list[dict]:
    """Return wiki articles, optionally including deleted.

    # TODO Phase 1
    """
    raise NotImplementedError


def get_article(conn: sqlite3.Connection, article_id: str) -> dict | None:
    """Return a wiki article row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_article(conn: sqlite3.Connection, *, article_id: str, **fields) -> None:
    """Insert a new wiki article.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_article(conn: sqlite3.Connection, article_id: str, **fields) -> None:
    """Update a wiki article.

    # TODO Phase 1
    """
    raise NotImplementedError


def get_image(conn: sqlite3.Connection, image_id: str) -> dict | None:
    """Return a wiki image row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_image(conn: sqlite3.Connection, *, image_id: str, **fields) -> None:
    """Insert a new wiki image.

    # TODO Phase 1
    """
    raise NotImplementedError
