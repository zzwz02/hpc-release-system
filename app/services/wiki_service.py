"""Wiki service — article and image CRUD.

Faithful port of release_system/wiki/core.py accessed via server.py.
All business logic stays in wiki_core; this service is a thin translator
that enforces role checks and converts exceptions for the router layer.

Timestamp convention (F1 fix)
------------------------------
wiki_core.save_article / set_pinned / delete_article all call the module-level
``now()`` helper which returns UTC-ISO.  Every other table in the system uses
naive Beijing time.  Rather than editing the frozen core we temporarily replace
``wiki_core.now`` with ``beijing_timestamp`` for the duration of each write call
via the ``_use_beijing_time()`` context manager.  Python resolves the bare name
``now`` inside each core function against the module's ``__dict__`` at call time,
so the patch takes effect without any bytecode change to wiki_core.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading

from release_system.wiki import core as wiki_core

# Guards the wiki_core.now monkeypatch window below.  FastAPI runs sync route
# handlers in an anyio threadpool, so two concurrent wiki writes could otherwise
# interleave: one thread's ``finally`` restoring ``now`` while another is still
# mid-write would let that write fall back to UTC.  Wiki writes are rare and
# quick (RM/Admin only), so serialising the patch window has negligible cost and
# makes the naive-Beijing guarantee deterministic.
_now_patch_lock = threading.Lock()


@contextlib.contextmanager
def _use_beijing_time():
    """Temporarily replace wiki_core.now() with beijing_timestamp().

    This makes INSERT/UPDATE statements inside the frozen wiki_core write naive
    Beijing timestamps (``YYYY-MM-DD HH:MM:SS``) instead of UTC-ISO strings.
    The patch is held under ``_now_patch_lock`` so concurrent wiki writes cannot
    race on the shared module global, and the original function is restored in
    the finally block even on exceptions.
    """
    from app.timeutil import beijing_timestamp as _bt

    with _now_patch_lock:
        _orig = wiki_core.now
        wiki_core.now = _bt
        try:
            yield
        finally:
            wiki_core.now = _orig


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
    # KeyError propagates to router.
    # _use_beijing_time() patches wiki_core.now so created_at/updated_at are
    # written as naive Beijing time rather than UTC-ISO (F1 fix).
    with _use_beijing_time():
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
    with _use_beijing_time():
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
    with _use_beijing_time():
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
    # F1: wiki_images.uploaded_at must also be naive Beijing (matched pair with
    # the migration's wiki_images column) so no wiki table holds UTC-ISO.
    with _use_beijing_time():
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
