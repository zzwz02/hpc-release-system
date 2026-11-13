"""App catalog: profile, app_info sync, history, dependencies.

  GET  /api/apps/owner-lookup          — owners by repo suffix + branch (Jenkins tooling)
  GET  /api/app-audit                  — App change history
  GET  /api/apps/detail                — app_info versions and official versions of one App
  POST /api/apps/profile               — optional Owner profile
  GET  /api/app-info/version           — one stored app_info version
  POST /api/app-info/sync              — sync one App now
  POST /api/app-info/sync-all          — start syncing every running App (RM)
  GET  /api/app-info/sync-status       — per-App sync state (RM)
  GET  /api/dependencies               — Apps by declared component
  GET  /api/dependencies/compat.csv    — compatibility list export
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.errors import AuthzError
from app.deps import get_db, require_capability, require_login, require_tab_access
from app.domain.permissions import has_capability
from app.services import catalog_service, state_service

router = APIRouter(tags=["apps"])
require_rm = require_capability("release.manage", message="RM role required")
require_sync = require_capability("app.app_info.sync", message="无权同步 app_info")
require_dependencies_tab = require_tab_access("dependencies", message="无权查看组件依赖")


@router.get("/api/apps/owner-lookup")
def api_apps_owner_lookup(
    git_url: str = Query(...),
    branch: str = Query(...),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return state_service.lookup_latest_app_owner(conn, git_url=git_url, branch=branch)


@router.get("/api/app-audit")
def api_app_audit(
    app_id: str = Query(...),
    release_id: str = Query(default=""),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {
        "entries": state_service.get_app_audit(
            conn, app_id=app_id, release_id=release_id, username=user["username"], role=user["role"]
        )
    }


@router.get("/api/apps/detail")
def api_app_detail(
    app_id: str = Query(...),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return catalog_service.app_detail(conn, app_id)


@router.post("/api/apps/profile")
def api_app_profile(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    app = catalog_service.update_profile(
        conn, body["app_id"], body.get("fields") or {}, user=user["username"], role=user["role"]
    )
    return {"app": app}


@router.get("/api/app-info/version")
def api_app_info_version(
    version_id: int = Query(...),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"version": catalog_service.get_version_summary(conn, version_id)}


@router.post("/api/app-info/sync")
def api_app_info_sync(
    body: dict,
    user: dict = Depends(require_sync),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    app = catalog_service.get_app_or_raise(conn, body["app_id"])
    if not has_capability(user["role"], "release.manage") and not catalog_service.is_owner(app, user["username"]):
        raise AuthzError("只能同步自己负责的 App")
    return catalog_service.sync_app(conn, app["id"], user=user["username"], force=bool(body.get("force")))


@router.post("/api/app-info/sync-all")
def api_app_info_sync_all(user: dict = Depends(require_rm)) -> dict:
    return catalog_service.start_sync_all(user=user["username"])


@router.get("/api/app-info/sync-status")
def api_app_info_sync_status(
    user: dict = Depends(require_rm),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {
        "apps": catalog_service.sync_status(conn),
        "job": catalog_service.sync_all_state(),
        "daily": {
            "time": catalog_service.settings.app_info_sync_time,
            "enabled": catalog_service.settings.app_info_sync_enabled,
            "last_run": catalog_service.sync_loop.last_run,
            "last_summary": catalog_service.sync_loop.last_summary,
        },
    }


@router.get("/api/dependencies")
def api_dependencies(
    source: Literal["official", "development"] = Query(default="official"),
    user: dict = Depends(require_dependencies_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return catalog_service.dependency_overview(conn, source)


@router.get("/api/dependencies/compat.csv")
def api_compat_csv(
    component: str = Query(default=""),
    user: dict = Depends(require_dependencies_tab),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    content = catalog_service.compat_list_csv(conn, component)
    name = f"compat_{component or 'all'}.csv"
    return Response(
        content="﻿" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
