"""State router — GET /api/state (full page state, existing contract).

Faithful port of server.py:340-342 + state_payload().
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.deps import get_db, require_login
from app.services.app_service import get_state

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
def api_state(
    release_id: str = Query(default=""),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return full page-state payload for the given (or latest) release."""
    return get_state(conn, user=user, release_id_param=release_id)
