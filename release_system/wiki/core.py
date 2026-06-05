from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from typing import Any


READ_ROLES = {"Owner", "RM", "Admin"}
WRITE_ROLES = {"RM", "Admin"}
MAX_TITLE_CHARS = 160
MAX_BODY_CHARS = 200_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return f"wiki_{uuid.uuid4().hex[:12]}"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wiki_articles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body_md TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            deleted INTEGER NOT NULL DEFAULT 0,
            deleted_by TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_wiki_articles_visible
            ON wiki_articles(deleted, pinned, updated_at);

        CREATE TABLE IF NOT EXISTS wiki_images (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        """
    )


def can_write(role: str | None) -> bool:
    return str(role or "") in WRITE_ROLES


def can_read(role: str | None) -> bool:
    return str(role or "") in READ_ROLES


def require_read(role: str | None) -> None:
    if not can_read(role):
        raise PermissionError("只有 Owner/RM/Admin 可以查看开发 WIKI")


def require_write(role: str | None) -> None:
    if not can_write(role):
        raise PermissionError("只有 RM/Admin 可以维护开发 WIKI")


def normalize_title(title: str | None) -> str:
    text = (title or "").strip()
    if not text:
        raise ValueError("文章标题不能为空")
    if len(text) > MAX_TITLE_CHARS:
        raise ValueError(f"文章标题不能超过 {MAX_TITLE_CHARS} 个字符")
    return text


def normalize_body(body_md: str | None) -> str:
    text = str(body_md or "").strip()
    if len(text) > MAX_BODY_CHARS:
        raise ValueError(f"文章内容不能超过 {MAX_BODY_CHARS} 个字符")
    return text


def row_to_article(row: sqlite3.Row) -> dict[str, Any]:
    article = dict(row)
    article["pinned"] = bool(article.get("pinned"))
    article["deleted"] = bool(article.get("deleted"))
    return article


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


def list_articles(conn: sqlite3.Connection, *, include_body: bool = False) -> list[dict[str, Any]]:
    cols = (
        "id, title, body_md, pinned, created_by, created_at, updated_by, updated_at, deleted"
        if include_body
        else "id, title, body_md, pinned, created_by, created_at, updated_by, updated_at, deleted"
    )
    rows = conn.execute(
        f"""
        SELECT {cols}
        FROM wiki_articles
        WHERE deleted = 0
        ORDER BY pinned DESC, updated_at DESC, created_at DESC
        """
    ).fetchall()
    articles = []
    for row in rows:
        article = row_to_article(row)
        article["excerpt"] = article_excerpt(article.get("body_md"))
        if not include_body:
            article.pop("body_md", None)
        articles.append(article)
    return articles


def get_article(conn: sqlite3.Connection, article_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, title, body_md, pinned, created_by, created_at,
               updated_by, updated_at, deleted
        FROM wiki_articles
        WHERE id = ? AND deleted = 0
        """,
        (article_id,),
    ).fetchone()
    if not row:
        raise KeyError("wiki article not found")
    return row_to_article(row)


def save_article(
    conn: sqlite3.Connection,
    *,
    article_id: str | None = None,
    title: str,
    body_md: str,
    pinned: bool = False,
    user: str,
    role: str,
) -> dict[str, Any]:
    require_write(role)
    final_title = normalize_title(title)
    final_body = normalize_body(body_md)
    ts = now()

    if article_id:
        row = conn.execute(
            "SELECT id FROM wiki_articles WHERE id = ? AND deleted = 0",
            (article_id,),
        ).fetchone()
        if not row:
            raise KeyError("wiki article not found")
        conn.execute(
            """
            UPDATE wiki_articles
            SET title = ?, body_md = ?, pinned = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND deleted = 0
            """,
            (final_title, final_body, 1 if pinned else 0, user, ts, article_id),
        )
        conn.commit()
        return get_article(conn, article_id)

    final_id = new_id()
    conn.execute(
        """
        INSERT INTO wiki_articles(
            id, title, body_md, pinned, created_by, created_at, updated_by, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (final_id, final_title, final_body, 1 if pinned else 0, user, ts, user, ts),
    )
    conn.commit()
    return get_article(conn, final_id)


def set_pinned(
    conn: sqlite3.Connection,
    article_id: str,
    pinned: bool,
    *,
    user: str,
    role: str,
) -> dict[str, Any]:
    require_write(role)
    row = conn.execute(
        "SELECT id FROM wiki_articles WHERE id = ? AND deleted = 0",
        (article_id,),
    ).fetchone()
    if not row:
        raise KeyError("wiki article not found")
    ts = now()
    conn.execute(
        """
        UPDATE wiki_articles
        SET pinned = ?, updated_by = ?, updated_at = ?
        WHERE id = ? AND deleted = 0
        """,
        (1 if pinned else 0, user, ts, article_id),
    )
    conn.commit()
    return get_article(conn, article_id)


def delete_article(
    conn: sqlite3.Connection,
    article_id: str,
    *,
    user: str,
    role: str,
) -> None:
    require_write(role)
    row = conn.execute(
        "SELECT id FROM wiki_articles WHERE id = ? AND deleted = 0",
        (article_id,),
    ).fetchone()
    if not row:
        raise KeyError("wiki article not found")
    ts = now()
    conn.execute(
        """
        UPDATE wiki_articles
        SET deleted = 1, deleted_by = ?, deleted_at = ?, updated_by = ?, updated_at = ?
        WHERE id = ? AND deleted = 0
        """,
        (user, ts, user, ts, article_id),
    )
    conn.commit()


def save_image(
    conn: sqlite3.Connection,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    require_write(role)
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("仅支持 PNG/JPEG/GIF/WebP 图片")
    if not content:
        raise ValueError("图片内容为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片不能超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
    image_id = f"wiki_img_{uuid.uuid4().hex[:16]}"
    final_name = (filename or image_id).strip()[:180]
    conn.execute(
        """
        INSERT INTO wiki_images(id, filename, content_type, content, uploaded_by, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (image_id, final_name, ctype, sqlite3.Binary(content), user, now()),
    )
    conn.commit()
    return {
        "id": image_id,
        "filename": final_name,
        "content_type": ctype,
        "url": f"/api/wiki/images/{image_id}",
        "bytes": len(content),
    }


def get_image(conn: sqlite3.Connection, image_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, filename, content_type, content, uploaded_by, uploaded_at
        FROM wiki_images
        WHERE id = ?
        """,
        (image_id,),
    ).fetchone()
    if not row:
        raise KeyError("wiki image not found")
    return dict(row)
