"""Wiki router — internal dev wiki CRUD.

Endpoints (faithful port of server.py):
  GET  /api/wiki/articles              — list all articles
  GET  /api/wiki/articles/{id}         — get one article (404 if not found)
  POST /api/wiki/articles/save         — create or update article
  POST /api/wiki/articles/pin          — toggle pinned flag
  POST /api/wiki/articles/delete       — soft-delete article
  POST /api/wiki/images/upload         — upload image (base64)
  GET  /api/wiki/images/{id}           — get image binary
"""
from __future__ import annotations

import base64
import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.deps import get_db, require_login
from app.services import wiki_service

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# ---------------------------------------------------------------------------
# GET /api/wiki/articles
# ---------------------------------------------------------------------------

def get_articles(
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """List all wiki articles.

    Mirrors server.py:357-361.
    """
    articles = wiki_service.list_articles(conn, role=user["role"])
    return {"articles": articles}


router.add_api_route("/articles", get_articles, methods=["GET"])


# ---------------------------------------------------------------------------
# GET /api/wiki/articles/{article_id}
# ---------------------------------------------------------------------------

def get_article(
    article_id: str,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Get a single wiki article.

    Mirrors server.py:381-391.
    Returns 404 JSON if not found.
    """
    try:
        article = wiki_service.get_article(conn, article_id, role=user["role"])
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "文章不存在"})
    return JSONResponse(content={"article": article})


router.add_api_route("/articles/{article_id}", get_article, methods=["GET"])


# ---------------------------------------------------------------------------
# POST /api/wiki/articles/save
# ---------------------------------------------------------------------------

async def post_save_article(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Create or update a wiki article.

    Mirrors server.py:596-614.
    Returns 404 JSON if article_id given but not found.
    """
    body = await request.json()
    try:
        article = wiki_service.create_or_update_article(
            conn,
            article_id=body.get("id") or None,
            title=body.get("title", ""),
            body_md=body.get("body_md", ""),
            pinned=bool(body.get("pinned")),
            user=user["username"],
            role=user["role"],
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "文章不存在"})
    return JSONResponse(content={"ok": True, "article": article})


router.add_api_route("/articles/save", post_save_article, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/wiki/articles/pin
# ---------------------------------------------------------------------------

async def post_pin_article(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Toggle the pinned flag on a wiki article.

    Mirrors server.py:616-632.
    Returns 404 JSON if not found.
    """
    body = await request.json()
    try:
        article = wiki_service.set_article_pinned(
            conn,
            body.get("id", ""),
            bool(body.get("pinned")),
            user=user["username"],
            role=user["role"],
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "文章不存在"})
    return JSONResponse(content={"ok": True, "article": article})


router.add_api_route("/articles/pin", post_pin_article, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/wiki/articles/delete
# ---------------------------------------------------------------------------

async def post_delete_article(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Soft-delete a wiki article.

    Mirrors server.py:634-649.
    Returns 404 JSON if not found.
    """
    body = await request.json()
    try:
        wiki_service.delete_article(
            conn,
            body.get("id", ""),
            user=user["username"],
            role=user["role"],
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "文章不存在"})
    return JSONResponse(content={"ok": True})


router.add_api_route("/articles/delete", post_delete_article, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/wiki/images/upload
# ---------------------------------------------------------------------------

async def post_upload_image(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Upload an image (base64-encoded).

    Mirrors server.py:651-668.
    """
    body = await request.json()
    content_b64 = body.get("content_base64", "")
    if not content_b64:
        raise ValueError("content_base64 required")
    image = wiki_service.save_image(
        conn,
        content=base64.b64decode(content_b64),
        filename=body.get("filename", ""),
        content_type=body.get("content_type", ""),
        user=user["username"],
        role=user["role"],
    )
    return {"ok": True, "image": image}


router.add_api_route("/images/upload", post_upload_image, methods=["POST"])


# ---------------------------------------------------------------------------
# GET /api/wiki/images/{image_id}
# ---------------------------------------------------------------------------

def get_image(
    image_id: str,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Return image binary data.

    Mirrors server.py:363-378.
    Returns 404 JSON if not found.
    """
    try:
        image = wiki_service.get_image(conn, image_id, role=user["role"])
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "图片不存在"})
    content: bytes = image["content"]
    headers = {
        "Content-Length": str(len(content)),
        "Cache-Control": "private, max-age=86400",
    }
    return Response(
        content=content,
        media_type=image["content_type"],
        headers=headers,
    )


router.add_api_route("/images/{image_id}", get_image, methods=["GET"])
