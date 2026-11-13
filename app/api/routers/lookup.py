"""Look up historical image commands or recorded App releases."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.deps import get_db, require_tab_access
from app.services import lookup_service

router = APIRouter(prefix="/api/lookup", tags=["lookup"])
require_lookup_tab = require_tab_access("lookup", message="无权查看历史查询")


@router.get("/releases")
def published_releases(
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"releases": lookup_service.published_releases(conn)}


@router.get("/release")
def published_apps(
    release_id: str = Query(...),
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return lookup_service.published_apps(conn, release_id)


@router.get("/app-info")
def app_info_source(
    image: str = Query(...),
    commit_id: str = Query(...),
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    document = lookup_service.app_info_source(conn, image, commit_id)
    return Response(
        json.dumps(document, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": 'inline; filename="app_info.json"', "Cache-Control": "no-store"},
    )


@router.get("/image")
def lookup_image(
    image: str = Query(...),
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return lookup_service.by_image(conn, image)


@router.get("/digest")
def lookup_digest(
    digest: str = Query(...),
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return lookup_service.by_digest(conn, digest)


@router.get("/app")
def lookup_app(
    app_id: str = Query(...),
    user: dict = Depends(require_lookup_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return lookup_service.by_app(conn, app_id)
