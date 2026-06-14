"""Wiki service — article and image CRUD.

Faithful port of release_system/wiki/core.py accessed via server.py.
All business logic stays in wiki_core; this service is a thin translator
that enforces role checks and converts exceptions for the router layer.
"""
from __future__ import annotations

import sqlite3

from release_system.wiki import core as wiki_core


def list_articles(conn: sqlite3.Connection, *, role: str) -> list[dict]:
    """Return visible wiki articles for the given role.

    Mirrors server.py:357-361.
    Raises AuthzError if role is not in READ_ROLES.
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.READ_ROLES:
        raise AuthzError("只有 Owner/RM/Admin 可以查看开发 WIKI")
    return wiki_core.list_articles(conn)


def get_article(conn: sqlite3.Connection, article_id: str, *, role: str) -> dict:
    """Return a single wiki article.

    Mirrors server.py:381-391.
    Raises AuthzError if role is not in READ_ROLES.
    Raises KeyError if article does not exist (caller maps to 404).
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.READ_ROLES:
        raise AuthzError("只有 Owner/RM/Admin 可以查看开发 WIKI")
    # KeyError propagates to router
    return wiki_core.get_article(conn, article_id)


def create_or_update_article(
    conn: sqlite3.Connection,
    *,
    article_id: str | None,
    title: str,
    body_md: str,
    pinned: bool,
    user: str,
    role: str,
) -> dict:
    """Create or update a wiki article.

    Mirrors server.py:596-614.
    Raises AuthzError if role is not in WRITE_ROLES.
    Raises KeyError if article_id is given but does not exist.
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.WRITE_ROLES:
        raise AuthzError("只有 RM/Admin 可以维护开发 WIKI")
    # KeyError propagates to router
    return wiki_core.save_article(
        conn,
        article_id=article_id,
        title=title,
        body_md=body_md,
        pinned=pinned,
        user=user,
        role=role,
    )


def set_article_pinned(
    conn: sqlite3.Connection,
    article_id: str,
    pinned: bool,
    *,
    user: str,
    role: str,
) -> dict:
    """Toggle the pinned flag on an article.

    Mirrors server.py:616-632.
    Raises AuthzError if role is not in WRITE_ROLES.
    Raises KeyError if article does not exist.
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.WRITE_ROLES:
        raise AuthzError("只有 RM/Admin 可以维护开发 WIKI")
    return wiki_core.set_pinned(conn, article_id, pinned, user=user, role=role)


def delete_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    user: str,
    role: str,
) -> None:
    """Soft-delete a wiki article.

    Mirrors server.py:634-649.
    Raises AuthzError if role is not in WRITE_ROLES.
    Raises KeyError if article does not exist.
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.WRITE_ROLES:
        raise AuthzError("只有 RM/Admin 可以维护开发 WIKI")
    wiki_core.delete_article(conn, article_id, user=user, role=role)


def save_image(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    user: str,
    role: str,
) -> dict:
    """Store an uploaded image.

    Mirrors server.py:651-668.
    Raises AuthzError if role is not in WRITE_ROLES.
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.WRITE_ROLES:
        raise AuthzError("只有 RM/Admin 可以维护开发 WIKI")
    return wiki_core.save_image(
        conn,
        content=content,
        filename=filename,
        content_type=content_type,
        user=user,
        role=role,
    )


def get_image(conn: sqlite3.Connection, image_id: str, *, role: str) -> dict:
    """Return a single image record.

    Mirrors server.py:363-378.
    Raises AuthzError if role is not in READ_ROLES.
    Raises KeyError if image does not exist (caller maps to 404).
    """
    from app.api.errors import AuthzError

    if role not in wiki_core.READ_ROLES:
        raise AuthzError("只有 Owner/RM/Admin 可以查看开发 WIKI")
    return wiki_core.get_image(conn, image_id)
