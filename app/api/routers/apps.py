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

import release_system.core as core
from app.api.errors import AuthzError
from app.deps import get_db, require_login
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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Create a new app request in a release.

    Mirrors server.py:759-773.
    Only Owner or RM can submit.
    """
    if user["role"] not in {"Owner", "RM"}:
        raise AuthzError("Only Owner or RM can submit new app requests")
    result = app_service.add_new_app(
        conn,
        release_id=body["release_id"],
        user=user["username"],
        official_name=body["official_name"],
        git_url=body["git_url"],
        git_branch=body["git_branch"],
        release_decision=body["release_decision"],
        doc_target=body.get("doc_target", "manual"),
        cicd_repo_type=body.get("cicd_repo_type", ""),
        cicd_community_artifact=body.get("cicd_community_artifact", ""),
        cicd_build_image=body.get("cicd_build_image", ""),
        cicd_test_timeout=body.get("cicd_test_timeout", ""),
        cicd_notes=body.get("cicd_notes", ""),
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
# POST /api/apps/decision-sync/preview
# ---------------------------------------------------------------------------

@router.post("/api/apps/decision-sync/preview")
def api_apps_decision_sync_preview(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Dry-run the decision-sync gating rule for the owner-choice dialog.

    Body: {release_id, app_id, decision}. Returns
    {decision, releases:[{release_id, release_name, phase_label,
    resulting_decision, skipped, reason?}], forced, scope}. No writes.

    Auth mirrors /api/apps/update: RM, or an Owner of the app in this release.
    """
    release = core.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    from app.services.authz import require_owner_or_rm_with_owners

    require_owner_or_rm_with_owners(snap.get("owners"), user["username"], user["role"])
    return app_service.preview_decision_sync(
        conn,
        release_id=body["release_id"],
        app_id=body["app_id"],
        decision=body["decision"],
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
    # Replicate require_owner_or_rm check from server.py:1203-1205
    release = core.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    if role != "RM":
        if role != "Owner" or username not in (snap.get("owners") or []):
            raise AuthzError("Owner permission required")

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
    # Replicate require_owner_or_rm check from server.py:1221-1224
    release = core.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    if role != "RM":
        if role != "Owner" or username not in (snap.get("owners") or []):
            raise AuthzError("Owner permission required")

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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Fetch app_info from Gerrit for all apps in a release (RM only).

    Mirrors server.py:1239-1243.  Uses require_rm() message exactly.
    """
    if user["role"] != "RM":
        raise AuthzError("RM role required")
    return app_service.fetch_all_app_infos(
        conn,
        release_id=body["release_id"],
        uploaded_by=user["username"],
        role=user["role"],
    )
