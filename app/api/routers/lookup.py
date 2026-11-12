"""Look up cycle, environment, test scope and commands by digest or App."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.deps import get_db, require_tab_access
from app.services import lookup_service

router = APIRouter(prefix="/api/lookup", tags=["lookup"])
require_lookup_tab = require_tab_access("lookup", message="无权查询")


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
