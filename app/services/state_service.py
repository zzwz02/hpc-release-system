"""Page-state read model and small App read endpoints.

``GET /api/state`` returns everything the release-management pages need for
one cycle: Apps (profile, CICD status, latest app_info, official version,
open status request), cycles, the selected cycle with its environment, base
images, entries and scope requests, plus documents and QA log metadata.
"""
from __future__ import annotations

import sqlite3
from pathlib import PurePosixPath
from typing import Any

from app.domain.app_types import list_app_types
from app.domain.permissions import has_capability
from app.repositories import app_info_repo, apps_repo, qa_repo, schedule_repo
from app.repositories.audit_repo import app_audit_log, qa_audit_logs_by_app
from app.services import catalog_service, cicd_service, cycle_service
from app.services.authz import require_app_audit_access


def _app_actions(app: dict[str, Any], *, role: str, username: str) -> list[str]:
    owner = username in (app.get("owners") or [])
    actions = []
    if has_capability(role, "app.edit.any") or (has_capability(role, "app.edit.owned") and owner):
        actions.append("app.edit")
        if has_capability(role, "app.edit.rm_fields"):
            actions.append("app.edit.rm_fields")
    if has_capability(role, "app.audit.view.any") or (has_capability(role, "app.audit.view.owned") and owner):
        actions.append("app.audit.view")
    return actions


def get_state(
    conn: sqlite3.Connection,
    *,
    user: dict,
    release_id_param: str = "",
    include_allowed_actions: bool = False,
) -> dict:
    releases = cycle_service.list_releases(conn)
    release_ids = {r["id"] for r in releases}
    latest = releases[-1]["id"] if releases else ""
    release_id = release_id_param if release_id_param in release_ids else latest

    apps = catalog_service.list_app_views(conn)
    for app in apps:
        status_request = cicd_service.open_status_request(conn, app["id"])
        app["open_status_request"] = (
            {"id": status_request["id"], "target_status": status_request["target_status"]}
            if status_request
            else None
        )
        if include_allowed_actions:
            app["allowed_actions"] = _app_actions(app, role=user["role"], username=user["username"])

    payload: dict[str, Any] = {
        "apps": apps,
        "app_types": list_app_types(),
        "releases": releases,
        "release": None,
        "artifacts": [],
        "user": {
            "username": user["username"],
            "role": user["role"],
            "display_name": user.get("display_name", ""),
        },
        "user_display_names": {
            row["username"]: row["display_name"]
            for row in conn.execute("SELECT username, display_name FROM users WHERE display_name <> ''")
        },
        "qa_log": None,
        "qa_audit_logs": {},
        "release_schedule": schedule_repo.list_schedule(conn),
    }
    if release_id:
        release = cycle_service.release_detail(conn, release_id)
        release["entries"] = cycle_service.list_entry_views(conn, release_id)
        release["scope_requests"] = cycle_service.list_scope_requests(conn, release_id)
        payload["release"] = release
        payload["artifacts"] = [
            dict(row)
            for row in conn.execute(
                "SELECT kind, name, final, generated_at FROM artifacts WHERE release_id = ?", (release_id,)
            )
        ]
        payload["qa_log"] = qa_repo.get_qa_log(conn, release_id)
        if has_capability(user["role"], "qa.audit.view"):
            app_ids = [e["app_id"] for e in release["entries"] if e["status"] == "active"]
            payload["qa_audit_logs"] = qa_audit_logs_by_app(conn, release_id, app_ids) if app_ids else {}
    return payload


def get_app_audit(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    release_id: str,
    username: str,
    role: str,
) -> list[dict]:
    require_app_audit_access(conn, app_id, username, role)
    return app_audit_log(conn, app_id, release_id)


# ---------------------------------------------------------------------------
# Owner lookup (used by Jenkins tooling)
# ---------------------------------------------------------------------------


def repo_suffix_for_lookup(git_url: str) -> str:
    value = str(git_url or "").strip().rstrip("/")
    if not value:
        return ""
    if value.lower().endswith(".xml"):
        return value.lstrip("/")
    suffix = PurePosixPath(value).name or value.rsplit("/", 1)[-1]
    return suffix[:-4] if suffix.endswith(".git") else suffix


def lookup_latest_app_owner(conn: sqlite3.Connection, *, git_url: str, branch: str) -> dict[str, Any]:
    """Owners, name and latest app_info version of Apps matching a repo suffix + branch."""
    suffix = repo_suffix_for_lookup(git_url)
    git_branch = str(branch or "").strip()
    if not suffix:
        raise ValueError("git_url is required")
    if not git_branch:
        raise ValueError("branch is required")
    matches = []
    for app in apps_repo.list_apps(conn):
        if app["git_branch"] != git_branch or not app["git_url"].endswith(suffix):
            continue
        latest = app_info_repo.latest_version(conn, app["id"])
        matches.append(
            {
                "app_id": app["id"],
                "git_url": app["git_url"],
                "git_branch": app["git_branch"],
                "owners": list(app.get("owners") or []),
                "official_name": app.get("official_name") or "",
                "version": str((latest or {}).get("raw", {}).get("app_version") or ""),
                "cicd_status": app["cicd_status"],
            }
        )
    return {
        "request_git_url": git_url,
        "request_git_branch": git_branch,
        "git_url": git_url,
        "repo_suffix": suffix,
        "branch": git_branch,
        "matches": matches,
    }
