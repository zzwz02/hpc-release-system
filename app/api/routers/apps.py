"""Apps router — app info and snapshot management.

Faithful port of server.py POST handlers at paths:
  /api/app-audit    (GET — shares /api prefix, lives here)
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

from app.deps import get_db, require_capability, require_login
from app.services import app_service, release_reads
from app.services.authz import require_owner_or_rm_with_owners

router = APIRouter(tags=["apps"])


# ---------------------------------------------------------------------------
# GET /api/apps/owner-lookup
# ---------------------------------------------------------------------------

@router.get("/api/apps/owner-lookup")
def api_apps_owner_lookup(
    git_url: str = Query(...),
    branch: str = Query(...),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return owners/name/version for latest-release apps by repo suffix + branch.

    Jenkins callers may pass either a short repo name or a full Gerrit URL; the
    service matches by the last path component and exact branch.
    """
    return app_service.lookup_latest_app_owner(conn, git_url=git_url, branch=branch)


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
    release = release_reads.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
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
    release = release_reads.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    require_owner_or_rm_with_owners(snap.get("owners"), username, role)

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
    release = release_reads.get_release(conn, body["release_id"])
    snap = release["snapshots"].get(body["app_id"], {})
    role = user["role"]
    username = user["username"]
    require_owner_or_rm_with_owners(snap.get("owners"), username, role)

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
    user: dict = Depends(
        require_capability("app.edit.rm_fields", message="RM role required")
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Fetch app_info from Gerrit for all apps in a release (RM only).

    Mirrors server.py:1239-1243.  Uses require_rm() message exactly.
    """
    return app_service.fetch_all_app_infos(
        conn,
        release_id=body["release_id"],
        uploaded_by=user["username"],
        role=user["role"],
    )
