"""Monthly cycles (RM) and the dashboard release timeline.

  GET  /api/releases/defaults      — default dates for a month
  POST /api/releases/create        — new empty cycle
  POST /api/releases/update        — name and dates
  POST /api/releases/environment   — replace the cycle environment
  POST /api/releases/base-image    — add / update a base image
  POST /api/releases/base-image/delete
  POST /api/releases/publish       — publish (results, official versions, final docs)
  POST /api/releases/unlock        — reopen a published cycle for corrections
  POST /api/release-schedule/upsert | delete
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.deps import get_db, require_capability
from app.domain import cycles as cycles_domain
from app.services import cycle_service, release_service

router = APIRouter(tags=["releases"])
require_release_management_access = require_capability(
    "release.manage",
    message="RM role required",
)


@router.get("/api/releases/defaults")
def get_release_defaults(
    month: str = Query(..., description="YYYY-MM"),
    user: dict = Depends(require_release_management_access),
) -> dict:
    try:
        year, mon = (int(part) for part in month.split("-"))
        return cycles_domain.default_dates(year, mon)
    except ValueError as exc:
        raise ValueError(f"月份格式错误：{month!r}，应为 YYYY-MM") from exc


@router.post("/api/releases/create")
def post_releases_create(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return cycle_service.create_cycle(
        conn,
        name=body.get("name", ""),
        signup_deadline=body.get("signup_deadline", ""),
        qa_deadline=body.get("qa_deadline", ""),
        release_date=body.get("release_date", ""),
        user=user["username"],
        role=user["role"],
    )


@router.post("/api/releases/update")
def post_releases_update(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {
        "release": cycle_service.update_cycle(
            conn,
            body["release_id"],
            name=body.get("name", ""),
            signup_deadline=body.get("signup_deadline", ""),
            qa_deadline=body.get("qa_deadline", ""),
            release_date=body.get("release_date", ""),
            user=user["username"],
            role=user["role"],
        )
    }


@router.post("/api/releases/environment")
def post_releases_environment(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = cycle_service.save_environment(
        conn, body["release_id"], body.get("rows") or [], user=user["username"], role=user["role"]
    )
    return {"environment": rows}


@router.post("/api/releases/base-image")
def post_releases_base_image(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    images = cycle_service.save_base_image(
        conn, body["release_id"], body.get("image") or {}, user=user["username"], role=user["role"]
    )
    return {"base_images": images}


@router.post("/api/releases/base-image/delete")
def post_releases_base_image_delete(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    images = cycle_service.delete_base_image(
        conn, body["release_id"], int(body["image_id"]), user=user["username"], role=user["role"]
    )
    return {"base_images": images}


@router.post("/api/releases/publish")
def post_releases_publish(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    result = cycle_service.publish(conn, body["release_id"], user=user["username"], role=user["role"])
    return {"released": result["released"], "failed": result["failed"], "artifacts": list(result["artifacts"])}


@router.post("/api/releases/unlock")
def post_releases_unlock(
    body: dict,
    user: dict = Depends(require_release_management_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return cycle_service.unlock(
        conn, body["release_id"], reason=body.get("reason", ""), user=user["username"], role=user["role"]
    )


# ---------------------------------------------------------------------------
# POST /api/release-schedule/upsert
# ---------------------------------------------------------------------------

@router.post("/api/release-schedule/upsert")
async def post_release_schedule_upsert(
    request: Request,
    user: dict = Depends(require_release_management_access),
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
    user: dict = Depends(require_release_management_access),
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
