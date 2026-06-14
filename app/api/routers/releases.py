"""Releases router — release lifecycle and schedule management.

Wave 2 endpoints (faithful port of server.py handlers):
  POST /api/import-initial          — bootstrap first release from CSV (RM only)
  POST /api/releases/create         — clone most-recent release (RM only)
  POST /api/releases/deadlines      — update name/deadlines (RM only)
  POST /api/releases/final-lock     — final lock + artifact generation (RM only)
  POST /api/releases/final-unlock   — reverse final lock (RM only)
  POST /api/release-schedule/upsert — create or update schedule entry (RM only)
  POST /api/release-schedule/delete — delete schedule entry (RM only)

R3 stubs (NOT implemented — Phase 4):
  GET /api/releases  — per-section refresh list
  GET /api/releases/{release_id}
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.deps import get_db, require_roles
from app.services import release_service

router = APIRouter(tags=["releases"])


# ---------------------------------------------------------------------------
# POST /api/import-initial  (lives outside /api/releases/ prefix)
# ---------------------------------------------------------------------------

@router.post("/api/import-initial")
async def post_import_initial(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Bootstrap the first release from a CSV payload.

    Body: {csv, release_name?, maca_version?, app_freeze_deadline?, doc_deadline?}
    Returns: {"release_id": ...}
    Mirrors server.py:669-682.
    """
    body = await request.json()
    result = release_service.import_initial(
        conn,
        csv=body.get("csv", ""),
        release_name=body.get("release_name") or None,
        maca_version=body.get("maca_version") or None,
        app_freeze_deadline=body.get("app_freeze_deadline", ""),
        doc_deadline=body.get("doc_deadline", ""),
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/releases/create
# ---------------------------------------------------------------------------

@router.post("/api/releases/create")
async def post_releases_create(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Clone the most recent release into a new one.

    Body: {name, maca_version?, app_freeze_deadline?, doc_deadline?}
    Returns: {"release_id": ...}
    Mirrors server.py:682-695.
    """
    body = await request.json()
    result = release_service.create_release(
        conn,
        name=body["name"],
        maca_version=body.get("maca_version", ""),
        app_freeze_deadline=body.get("app_freeze_deadline", ""),
        doc_deadline=body.get("doc_deadline", ""),
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/releases/deadlines
# ---------------------------------------------------------------------------

@router.post("/api/releases/deadlines")
async def post_releases_deadlines(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Update a release's name and/or deadline fields.

    Body: {release_id, name?, app_freeze_deadline?, doc_deadline?}
    Returns: {"release": <serialized release with phase>}
    Mirrors server.py:696-709.
    """
    body = await request.json()
    result = release_service.update_deadlines(
        conn,
        release_id=body["release_id"],
        name=body.get("name"),
        app_freeze_deadline=body.get("app_freeze_deadline"),
        doc_deadline=body.get("doc_deadline"),
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/releases/final-lock
# ---------------------------------------------------------------------------

@router.post("/api/releases/final-lock")
async def post_releases_final_lock(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Final-lock a release and generate final artifacts.

    Body: {release_id}
    Returns: {"artifacts": [...]}
    Mirrors server.py:710-715.
    """
    body = await request.json()
    result = release_service.final_lock(
        conn,
        release_id=body["release_id"],
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/releases/final-unlock
# ---------------------------------------------------------------------------

@router.post("/api/releases/final-unlock")
async def post_releases_final_unlock(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Reverse a final lock.

    Body: {release_id}
    Returns: {"ok": true}
    Mirrors server.py:716-721.
    """
    body = await request.json()
    result = release_service.final_unlock(
        conn,
        release_id=body["release_id"],
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/release-schedule/upsert
# ---------------------------------------------------------------------------

@router.post("/api/release-schedule/upsert")
async def post_release_schedule_upsert(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Create or update a release schedule entry.

    Body: {id?, version, branch_cut_at?, release_at?, note?}
    Returns: {"entry": <entry dict>}
    Mirrors server.py:995-1009.
    """
    body = await request.json()
    result = release_service.upsert_schedule_entry(
        conn,
        entry_id=body.get("id") or None,
        version=body.get("version", ""),
        branch_cut_at=body.get("branch_cut_at", ""),
        release_at=body.get("release_at", ""),
        note=body.get("note", ""),
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/release-schedule/delete
# ---------------------------------------------------------------------------

@router.post("/api/release-schedule/delete")
async def post_release_schedule_delete(
    request: Request,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Delete a release schedule entry.

    Body: {id}
    Returns: {"ok": true}
    Mirrors server.py:1010-1017.
    """
    body = await request.json()
    result = release_service.delete_schedule_entry(
        conn,
        entry_id=body.get("id", ""),
        user=user["username"],
        role=user["role"],
    )
    return JSONResponse(result)
