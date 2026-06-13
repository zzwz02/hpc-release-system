"""Apps router — app info and snapshot management.

Faithful port of server.py POST handlers at paths:
  /api/app-audit    (GET — shares /api prefix, lives here)
  /api/apps/new
  /api/apps/update
  /api/app-info
  /api/app-info/fetch
  /api/app-info/fetch-all

Note: GET /api/app-audit is a GET endpoint mounted under the top-level
/api prefix, not /api/apps.  We include it in this router for cohesion
since it belongs to the apps workbench slice.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.deps import get_db, require_login, require_roles
from app.services import app_service

router = APIRouter(tags=["apps"])


# ---------------------------------------------------------------------------
# GET /api/app-audit
# ---------------------------------------------------------------------------

@router.get("/api/app-audit")
def api_app_audit(
    app_id: str = Query(...),
    release_id: str = Query(default=""),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return audit log entries for an app (optionally filtered to one release).

    Mirrors server.py:393-400.
    """
    if not app_id:
        raise ValueError("app_id is required")
    entries = app_service.get_app_audit(
        conn,
        app_id=app_id,
        release_id=release_id,
        username=user["username"],
        role=user["role"],
    )
    return {"entries": entries}


# ---------------------------------------------------------------------------
# POST /api/apps/new
# ---------------------------------------------------------------------------

@router.post("/api/apps/new")
def api_apps_new(
    body: dict,
    user: dict = Depends(require_roles("Owner", "RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Create a new app request in a release.

    Mirrors server.py:759-773.
    Only Owner or RM can submit.
    """
    result = app_service.add_new_app(
        conn,
        release_id=body["release_id"],
        user=user["username"],
        official_name=body["official_name"],
        git_url=body["git_url"],
        git_branch=body["git_branch"],
        release_decision=body["release_decision"],
        doc_target=body.get("doc_target", "manual"),
    )
    return result


# ---------------------------------------------------------------------------
# POST /api/apps/update
# ---------------------------------------------------------------------------

@router.post("/api/apps/update")
def api_apps_update(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Update a snapshot (fields + optional release_decision).

    Mirrors server.py:775-939.
    Auth is enforced inside update_snapshot (require_owner_or_rm).
    """
    return app_service.update_snapshot(
        conn,
        body["release_id"],
        body["app_id"],
        user=user["username"],
        role=user["role"],
        fields=body,
    )


# ---------------------------------------------------------------------------
# POST /api/app-info
# ---------------------------------------------------------------------------

@router.post("/api/app-info")
def api_app_info(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Apply owner-uploaded app_info to a snapshot.

    Mirrors server.py:1200-1216.
    Auth enforced inside apply_app_info (require_owner_or_rm on snapshot owners).
    """
    import release_system.core as core
    from app.api.errors import AuthzError as _AuthzError

    # Replicate require_owner_or_rm check from server.py:1203-1205
    release = core.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    if role != "RM":
        if role != "Owner" or username not in (snap.get("owners") or []):
            raise _AuthzError("Owner permission required")

    return app_service.apply_app_info(
        conn,
        release_id=body["release_id"],
        app_id=body["app_id"],
        app_info=body["app_info"],
        source=body.get("source", "owner upload"),
        source_type="owner_upload",
        uploaded_by=username,
        role=role,
    )


# ---------------------------------------------------------------------------
# POST /api/app-info/fetch
# ---------------------------------------------------------------------------

@router.post("/api/app-info/fetch")
def api_app_info_fetch(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Fetch app_info from Gerrit and apply it to a snapshot.

    Mirrors server.py:1218-1237.
    """
    import release_system.core as core
    from app.api.errors import AuthzError as _AuthzError

    # Replicate require_owner_or_rm check from server.py:1221-1224
    release = core.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    if role != "RM":
        if role != "Owner" or username not in (snap.get("owners") or []):
            raise _AuthzError("Owner permission required")

    return app_service.fetch_app_info(
        conn,
        release_id=body["release_id"],
        app_id=body["app_id"],
        uploaded_by=username,
        role=role,
    )


# ---------------------------------------------------------------------------
# POST /api/app-info/fetch-all
# ---------------------------------------------------------------------------

@router.post("/api/app-info/fetch-all")
def api_app_info_fetch_all(
    body: dict,
    user: dict = Depends(require_roles("RM")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Fetch app_info from Gerrit for all apps in a release (RM only).

    Mirrors server.py:1239-1243.
    """
    return app_service.fetch_all_app_infos(
        conn,
        release_id=body["release_id"],
        uploaded_by=user["username"],
        role=user["role"],
    )
