"""Wiki service — article and image CRUD.

Full port of release_system/wiki/core.py business rules onto wiki_repo.
Role checks raise AuthzError for the router layer; KeyError means "not found"
(router maps to 404).

Timestamp convention (F1 fix): all writes use naive Beijing time via
``beijing_timestamp()`` — the legacy UTC-ISO ``now()`` is not used.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from app.api.errors import AuthzError
from app.db.connection import transaction
from app.repositories import wiki_repo
from app.timeutil import beijing_timestamp

READ_ROLES = {"Owner", "RM", "Admin"}
WRITE_ROLES = {"RM", "Admin"}
MAX_TITLE_CHARS = 160
MAX_BODY_CHARS = 200_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

_READ_DENIED = "只有 Owner/RM/Admin 可以查看开发 WIKI"
_WRITE_DENIED = "只有 RM/Admin 可以维护开发 WIKI"


# ---------------------------------------------------------------------------
# Pure helpers — mirror wiki/core.py
# ---------------------------------------------------------------------------

def _normalize_title(title: str | None) -> str:
    text = (title or "").strip()
    if not text:
        raise ValueError("文章标题不能为空")
    if len(text) > MAX_TITLE_CHARS:
        raise ValueError(f"文章标题不能超过 {MAX_TITLE_CHARS} 个字符")
    return text


def _normalize_body(body_md: str | None) -> str:
    text = str(body_md or "").strip()
    if len(text) > MAX_BODY_CHARS:
        raise ValueError(f"文章内容不能超过 {MAX_BODY_CHARS} 个字符")
    return text


def article_excerpt(body_md: str | None, limit: int = 160) -> str:
    text = str(body_md or "")
    lines = []
    in_code = False
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped.startswith("!"):
            continue
        lines.append(stripped)
    excerpt = " ".join(lines).strip()
    return excerpt[:limit].rstrip() + ("..." if len(excerpt) > limit else "")


def _require_article(conn: sqlite3.Connection, article_id: str) -> dict[str, Any]:
    article = wiki_repo.get_article(conn, article_id)
    if not article:
        raise KeyError("wiki article not found")
    return article


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def list_articles(conn: sqlite3.Connection, *, role: str) -> list[dict]:
    """Return visible wiki articles (with excerpt, without body) for the role.

    Mirrors server.py:357-361.
    """
    if role not in READ_ROLES:
        raise AuthzError(_READ_DENIED)
    articles = []
    for article in wiki_repo.list_articles(conn):
        article["excerpt"] = article_excerpt(article.get("body_md"))
        article.pop("body_md", None)
        articles.append(article)
    return articles


def get_article(conn: sqlite3.Connection, article_id: str, *, role: str) -> dict:
    """Return a single wiki article.

    Raises AuthzError if role is not in READ_ROLES; KeyError if missing.
    """
    if role not in READ_ROLES:
        raise AuthzError(_READ_DENIED)
    return _require_article(conn, article_id)


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

    Raises AuthzError if role is not in WRITE_ROLES.
    Raises KeyError if article_id is given but does not exist.
    """
    if role not in WRITE_ROLES:
        raise AuthzError(_WRITE_DENIED)
    final_title = _normalize_title(title)
    final_body = _normalize_body(body_md)
    ts = beijing_timestamp()

    if article_id:
        _require_article(conn, article_id)
        with transaction(conn):
            wiki_repo.update_article(
                conn,
                article_id,
                title=final_title,
                body_md=final_body,
                pinned=1 if pinned else 0,
                updated_by=user,
                updated_at=ts,
            )
        return _require_article(conn, article_id)

    final_id = f"wiki_{uuid.uuid4().hex[:12]}"
    with transaction(conn):
        wiki_repo.insert_article(
            conn,
            article_id=final_id,
            title=final_title,
            body_md=final_body,
            pinned=1 if pinned else 0,
            created_by=user,
            created_at=ts,
        )
    return _require_article(conn, final_id)


def set_article_pinned(
    conn: sqlite3.Connection,
    article_id: str,
    pinned: bool,
    *,
    user: str,
    role: str,
) -> dict:
    """Toggle the pinned flag on an article."""
    if role not in WRITE_ROLES:
        raise AuthzError(_WRITE_DENIED)
    _require_article(conn, article_id)
    with transaction(conn):
        wiki_repo.update_pinned(
            conn,
            article_id,
            pinned=1 if pinned else 0,
            updated_by=user,
            updated_at=beijing_timestamp(),
        )
    return _require_article(conn, article_id)


def delete_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    user: str,
    role: str,
) -> None:
    """Soft-delete a wiki article."""
    if role not in WRITE_ROLES:
        raise AuthzError(_WRITE_DENIED)
    _require_article(conn, article_id)
    with transaction(conn):
        wiki_repo.soft_delete_article(
            conn,
            article_id,
            deleted_by=user,
            deleted_at=beijing_timestamp(),
        )


def save_image(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    user: str,
    role: str,
) -> dict:
    """Store an uploaded image (naive-Beijing uploaded_at, F1 fix)."""
    if role not in WRITE_ROLES:
        raise AuthzError(_WRITE_DENIED)
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("仅支持 PNG/JPEG/GIF/WebP 图片")
    if not content:
        raise ValueError("图片内容为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片不能超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
    image_id = f"wiki_img_{uuid.uuid4().hex[:16]}"
    final_name = (filename or image_id).strip()[:180]
    with transaction(conn):
        wiki_repo.insert_image(
            conn,
            image_id=image_id,
            filename=final_name,
            content_type=ctype,
            content=sqlite3.Binary(content),
            uploaded_by=user,
            uploaded_at=beijing_timestamp(),
        )
    return {
        "id": image_id,
        "filename": final_name,
        "content_type": ctype,
        "url": f"/api/wiki/images/{image_id}",
        "bytes": len(content),
    }


def get_image(conn: sqlite3.Connection, image_id: str, *, role: str) -> dict:
    """Return a single image record.

    Raises AuthzError if role is not in READ_ROLES; KeyError if missing.
    """
    if role not in READ_ROLES:
        raise AuthzError(_READ_DENIED)
    image = wiki_repo.get_image(conn, image_id)
    if not image:
        raise KeyError("wiki image not found")
    return image
