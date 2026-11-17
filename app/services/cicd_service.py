"""CICD requests: create / modify / start-stop, approval and SPD delivery.

CICD running status is a property of the App (``apps.cicd_status``) and
changes only when a status request takes effect (RM approval, or SPD delivery
for dispatched requests).  Release participation is a separate fact (signup
entries, ``cycle_service``).  The two never trigger each other; the only
coupling is one-way and checked in the same transaction as the write:

1. signing up requires ``running``                     (cycle_service.signup)
2. no signup while a stop request is open               (cycle_service.signup)
3. no stop request while the App has an active signup in an unpublished cycle
4. at most one open status request per App
7. a new App row is created only when its create request takes effect,
   with status ``running``.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from app.config import settings
from app.db.connection import connect, run_after_commit, transaction
from app.domain import app_info as app_info_domain
from app.domain import cicd_config as cicd_config_domain
from app.domain import cicd_requests as cicd_requests_domain
from app.domain.app_profile import DOC_TARGET_DEFAULT, normalize_doc_target
from app.domain.cycles import CICD_RUNNING, CICD_STOPPED
from app.domain.permissions import roles_for_capability
from app.domain.textutil import normalize_name, order_chips
from app.identity import validate_repo_path_input
from app.repositories import apps_repo, cicd_repo
from app.repositories.audit_repo import log_audit
from app.services import catalog_service
from app.timeutil import beijing_timestamp

CICD_APPROVER_ROLES = frozenset(roles_for_capability("cicd.request.approve"))
CICD_CREATE_ROLES = frozenset(roles_for_capability("cicd.request.submit"))
CICD_DELIVER_ROLES = frozenset(roles_for_capability("cicd.delivery.confirm"))
CICD_RETURN_ROLES = frozenset(roles_for_capability("cicd.delivery.return"))

CICD_STATUSES = (CICD_RUNNING, CICD_STOPPED)

_PAYLOAD_FIELDS = frozenset(
    {
        "app_name",
        "app_version",
        "repo_type",
        "repo_name",
        "branch",
        "build_product",
        "community_artifact",
        "build_image",
        "test_timeout",
        "owner_username",
        "status",
        "notes",
        "doc_target",
        "app_type",
    }
)

# Fields a modify request may change on an existing App.
_MUTABLE_FIELDS = frozenset(
    {"repo_type", "repo_name", "branch", "community_artifact", "build_image", "test_timeout", "notes", "status"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_payload_fields(payload: dict) -> None:
    for field in payload or {}:
        if field not in _PAYLOAD_FIELDS:
            raise RuntimeError(f"不支持的 CICD 字段：{field}")


def _get_request(conn: sqlite3.Connection, req_id: int) -> dict:
    row = conn.execute("SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    return dict(row)


def _request_out(conn: sqlite3.Connection, req_id: int) -> dict:
    return _strip_request(_get_request(conn, req_id))


def _strip_request(r: dict) -> dict:
    if r.get("app_id") and not r.get("task_id"):
        r["task_id"] = r["app_id"]
    return r


def _app_cicd_tasks(conn: sqlite3.Connection) -> list[dict]:
    from app.repositories import app_info_repo

    latest_ids = app_info_repo.latest_version_ids(conn)
    pending = cicd_repo.pending_task_ids(conn)
    delivery = cicd_repo.delivery_pending_task_ids(conn)
    display = {
        row["username"]: row["display_name"]
        for row in conn.execute("SELECT username, display_name FROM users WHERE display_name <> ''")
    }
    tasks = []
    for app in apps_repo.list_apps(conn):
        owners = app.get("owners") or []
        owner = owners[0] if owners else ""
        version = app_info_repo.get_version(conn, latest_ids.get(app["id"]))
        app_version = ""
        if version:
            app_version = str(version["raw"].get("app_version") or "")
        tasks.append(
            {
                "id": app["id"],
                "app_id": app["id"],
                "app_name": catalog_service.app_name(app),
                "app_version": app_version,
                "repo_type": cicd_config_domain.normalize_repo_type(app.get("cicd_repo_type")),
                "repo_name": app.get("git_url", ""),
                "branch": app.get("git_branch", ""),
                "build_product": [],
                "community_artifact": cicd_config_domain.normalize_community_artifacts(
                    app.get("cicd_community_artifact")
                ),
                "build_image": app.get("cicd_build_image", ""),
                "test_timeout": cicd_config_domain.normalize_test_timeout(app.get("cicd_test_timeout")),
                "owner_username": owner,
                "owner_display": display.get(owner, owner),
                "status": app.get("cicd_status", CICD_RUNNING),
                "notes": app.get("cicd_notes", ""),
                "created_at": app.get("created_at", ""),
                "updated_at": app.get("created_at", ""),
                "has_pending": app["id"] in pending,
                "has_pending_delivery": app["id"] in delivery,
            }
        )
    return tasks


def _attach_task_info(conn: sqlite3.Connection, items: list[dict]) -> None:
    tasks = {task["id"]: task for task in _app_cicd_tasks(conn)}
    for item in items:
        if item.get("app_id") and not item.get("task_id"):
            item["task_id"] = item["app_id"]
        task = tasks.get(item.get("app_id") or item.get("task_id") or "")
        if task:
            item["task_app_name"] = task["app_name"]
            item["task_app_version"] = task["app_version"]
            item["task_repo_name"] = task["repo_name"]
            item["task_branch"] = task["branch"]
            item["task_status"] = task["status"]
        else:
            payload = cicd_requests_domain.payload_object(item)
            item["task_app_name"] = payload.get("app_name", "")
            item["task_app_version"] = payload.get("app_version", "")
            item["task_repo_name"] = payload.get("repo_name", "")
            item["task_branch"] = payload.get("branch", "")
            item["task_status"] = payload.get("status", "")


def _find_app_by_identity(conn: sqlite3.Connection, git_url: str, git_branch: str) -> dict | None:
    from app.identity import same_identity

    for row in conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall():
        if same_identity(row["git_url"], row["git_branch"], git_url, git_branch):
            return dict(row)
    return None


def _open_create_for_identity(conn: sqlite3.Connection, repo_name: str, branch: str) -> dict | None:
    from app.identity import same_identity

    for row in conn.execute(
        "SELECT * FROM cicd_task_requests WHERE request_type = 'create'"
        " AND (status = 'pending' OR delivery_status IN ('pending', 'returned'))"
    ).fetchall():
        payload = cicd_requests_domain.payload_object(dict(row))
        if same_identity(str(payload.get("repo_name") or ""), str(payload.get("branch") or ""), repo_name, branch):
            return dict(row)
    return None


def open_status_request(conn: sqlite3.Connection, app_id: str) -> dict | None:
    """The App's open start/stop request with its ``target_status``, if any."""
    rows = cicd_repo.open_modifies_on_field(conn, app_id, "status")
    if not rows:
        return None
    row = dict(rows[0])
    _, row["target_status"] = cicd_requests_domain.status_change(row)
    return row


def _open_jira_modify(conn: sqlite3.Connection, app_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM cicd_task_requests
        WHERE request_type = 'modify' AND COALESCE(app_id, task_id) = ?
          AND TRIM(COALESCE(jira_id, '')) <> ''
          AND (status = 'pending' OR delivery_status IN ('pending', 'returned'))
        ORDER BY id LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    return dict(row) if row else None


def _replaceable_pending_modifies(conn: sqlite3.Connection, app_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE request_type = 'modify'"
        " AND COALESCE(app_id, task_id) = ? AND status = 'pending' ORDER BY id",
        (app_id,),
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if cicd_requests_domain.is_replaceable(dict(row)) and not cicd_requests_domain.has_status_change(dict(row))
    ]


def _validate_modify_repo_input(conn: sqlite3.Connection, app_id: str, payload: dict) -> None:
    if "repo_name" not in payload and "repo_type" not in payload:
        return
    app = apps_repo.get_app(conn, app_id) or {}
    type_change = payload.get("repo_type")
    name_change = payload.get("repo_name")
    next_type = cicd_config_domain.normalize_repo_type(
        type_change.get("new") if isinstance(type_change, dict) else app.get("cicd_repo_type")
    )
    next_name = str(name_change.get("new") if isinstance(name_change, dict) else app.get("git_url") or "").strip()
    if not next_name:
        raise ValueError("repo_name 不能为空，请检查 repo_name / branch")
    validate_repo_path_input(next_type, next_name)


# ---------------------------------------------------------------------------
# Applying a request
# ---------------------------------------------------------------------------


def _new_app_id(conn: sqlite3.Connection, official_name: str, repo_name: str) -> str:
    base = normalize_name(official_name) or normalize_name(repo_name.rsplit("/", 1)[-1]) or "app"
    used = apps_repo.all_app_ids(conn)
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _apply_create(conn: sqlite3.Connection, req: dict, payload: dict, *, user: str, role: str, ts: str) -> str:
    from app.identity import repo_storage_path, repo_to_git_identity

    repo_type = cicd_config_domain.normalize_repo_type(payload.get("repo_type"))
    repo_name = str(payload.get("repo_name") or "").strip().lstrip("/")
    branch = str(payload.get("branch") or "").strip()
    storage_type = "repo" if repo_type == "repo" or repo_name.endswith(".xml") else "git"
    git_url = repo_storage_path(storage_type, repo_name)
    if storage_type == "git":
        _, resolved_branch = repo_to_git_identity("git", repo_name, branch, refresh_manifest=False)
        branch = resolved_branch or branch
    existing = _find_app_by_identity(conn, git_url, branch)
    if existing:
        raise RuntimeError(f"该 Gerrit URL + branch 已存在 app（{existing['id']}），申请不能生效")
    app_id = _new_app_id(conn, str(payload.get("app_name") or ""), repo_name)
    owner = str(payload.get("owner_username") or req["submitter"]).strip()
    catalog_service.create_app(
        conn,
        app_id=app_id,
        official_name=str(payload.get("app_name") or app_id).strip(),
        git_url=git_url,
        git_branch=branch,
        owners=[owner] if owner else [],
        doc_target=str(payload.get("doc_target") or DOC_TARGET_DEFAULT),
        app_type=str(payload.get("app_type") or ""),
        created_by=req["submitter"],
        ts=ts,
    )
    apps_repo.update_cicd_config(
        conn,
        app_id,
        cicd_config_domain.normalize_app_config(
            {
                "cicd_repo_type": repo_type,
                "cicd_community_artifact": payload.get("community_artifact"),
                "cicd_build_image": payload.get("build_image", ""),
                "cicd_test_timeout": payload.get("test_timeout", ""),
                "cicd_notes": payload.get("notes", ""),
            }
        ),
    )
    conn.execute("UPDATE cicd_task_requests SET app_id = ?, task_id = ? WHERE id = ?", (app_id, app_id, req["id"]))
    log_audit(conn, f"CICD 新建申请 #{req['id']} 生效，创建 App", ts=ts, user=user, role=role,
              app_id=app_id, event="create_app")
    run_after_commit(conn, lambda: _sync_in_background(app_id))
    return app_id


def _sync_in_background(app_id: str) -> None:
    """Fetch a new App's app_info without blocking the request."""
    if not settings.app_info_sync_enabled:
        return

    def _run() -> None:
        conn = connect(settings.db_path)
        try:
            catalog_service.sync_app(conn, app_id, user="system")
        finally:
            conn.close()

    threading.Thread(target=_run, name=f"app-info-sync-{app_id}", daemon=True).start()


def _apply_modify(conn: sqlite3.Connection, req: dict, payload: dict, *, user: str, role: str, ts: str) -> str:
    from app.identity import repo_storage_path

    app_id = req.get("app_id") or req.get("task_id")
    app = apps_repo.get_app(conn, app_id) if app_id else None
    if not app:
        raise RuntimeError("CICD 修改申请缺少有效 app_id")
    config_updates: dict[str, Any] = {}
    git_url, git_branch = None, None
    for field, change in payload.items():
        if not isinstance(change, dict):
            continue
        if field == "repo_name":
            git_url = str(change.get("new") or "").strip()
        elif field == "branch":
            git_branch = str(change.get("new") or "").strip()
        elif field == "status":
            continue
        else:
            app_field = cicd_config_domain.CICD_PAYLOAD_FIELD_TO_APP_FIELD.get(field)
            if app_field:
                config_updates[app_field] = change.get("new")
    config_updates = cicd_config_domain.normalize_app_config(config_updates)
    if git_url is not None or git_branch is not None:
        next_type = cicd_config_domain.normalize_repo_type(config_updates.get("cicd_repo_type") or app.get("cicd_repo_type"))
        url = app["git_url"]
        if git_url is not None:
            storage_type = "repo" if next_type == "repo" or git_url.endswith(".xml") else "git"
            url = repo_storage_path(storage_type, git_url)
        branch = git_branch if git_branch is not None else app["git_branch"]
        collision = conn.execute(
            "SELECT id FROM apps WHERE git_url = ? AND git_branch = ? AND id != ?", (url, branch, app_id)
        ).fetchone()
        if collision:
            raise RuntimeError(f"该 Gerrit URL + branch 已被 app {collision['id']} 占用，不能改成相同值")
        apps_repo.update_identity(conn, app_id, git_url=url, git_branch=branch)
    apps_repo.update_cicd_config(conn, app_id, config_updates)
    _, target = cicd_requests_domain.status_change(req, payload)
    if target:
        if target == CICD_STOPPED:
            from app.services import cycle_service

            # Invariant (rules 2–3 are enforced at submit/signup time).
            signed_up = cycle_service.has_open_signup(conn, app_id)
            if signed_up:
                raise RuntimeError(
                    f"该 App 仍报名了未发布的版本（{'、'.join(signed_up)}），停止申请不能生效；请先撤回报名"
                )
        catalog_service.set_cicd_status(conn, app_id, target, user=user, role=role, ts=ts, request_id=req["id"])
    return app_id


def _apply_request(conn: sqlite3.Connection, req: dict, *, user: str, role: str, ts: str) -> str:
    payload = cicd_requests_domain.payload_object(req)
    payload = dict(payload)
    _validate_payload_fields(payload)
    if req["request_type"] == "create":
        return _apply_create(conn, req, payload, user=user, role=role, ts=ts)
    return _apply_modify(conn, req, payload, user=user, role=role, ts=ts)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_tasks(conn: sqlite3.Connection, *, status_filter: str | None = None) -> list[dict]:
    tasks = _app_cicd_tasks(conn)
    if status_filter in CICD_STATUSES:
        tasks = [task for task in tasks if task["status"] == status_filter]
    return tasks


def get_task_history(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    items = cicd_repo.list_requests(conn, task_id=task_id, status_filter="approved",
                                    approver_roles=CICD_APPROVER_ROLES)
    _attach_task_info(conn, items)
    return [_strip_request(r) for r in items]


def list_requests(
    conn: sqlite3.Connection,
    *,
    username: str | None = None,
    role: str = "Owner",
    task_id: str | None = None,
    status_filter: str | None = None,
    since_days: int | None = None,
    exclude_cancelled: bool = False,
) -> list[dict]:
    since_cutoff: str | None = None
    if since_days:
        import datetime as _dt

        from app.timeutil import beijing_now

        since_cutoff = (beijing_now() - _dt.timedelta(days=since_days)).strftime("%Y-%m-%d %H:%M:%S")
    items = cicd_repo.list_requests(
        conn,
        username=username,
        role=role,
        task_id=task_id,
        status_filter=status_filter,
        since_cutoff=since_cutoff,
        exclude_cancelled=exclude_cancelled,
        approver_roles=CICD_APPROVER_ROLES,
    )
    _attach_task_info(conn, items)
    return [_strip_request(r) for r in items]


def get_notifications(conn: sqlite3.Connection, username: str, role: str) -> dict:
    return cicd_repo.notification_counts(conn, username, role, approver_roles=CICD_APPROVER_ROLES,
                                         delivery_roles=CICD_RETURN_ROLES)


def list_deliveries(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
    role: str = "SPD",
    submitter: str | None = None,
) -> list[dict]:
    items = cicd_repo.list_deliveries(conn, status_filter=status_filter, submitter=submitter)
    _attach_task_info(conn, items)
    return [_strip_request(r) for r in items]


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def ensure_can_open_modify_request(conn: sqlite3.Connection, app_id: str, *, status_request: bool) -> None:
    """Reject a new modify request while another workflow on the App is open."""
    jira = _open_jira_modify(conn, app_id)
    if jira:
        raise RuntimeError(
            f"已有 Jira 申请 #{jira['id']} / {jira['jira_id']} 正在交付，不能提交新的 CICD 修改。"
            "请先让 SPD 退回需求，再由 RM 在待交付页拒绝旧申请。"
        )
    status_req = open_status_request(conn, app_id)
    if status_req:
        kind = "启动/停止" if status_request else "运行/停止状态"
        raise RuntimeError(
            f"该 App 已有{kind}申请 #{status_req['id']} 未完成，不能提交新的 CICD 修改；请先等待该申请处理完成"
        )


def submit_request(
    conn: sqlite3.Connection,
    *,
    task_id: str | None,
    request_type: str,
    payload: dict,
    submitter: str,
    submitter_role: str,
    submitter_display: str = "",
    source: str = "cicd_workbench",
    replace_open: bool = False,
) -> dict:
    """Submit a CICD modify request (config change or start/stop).

    New Apps use ``submit_new_app``.  A status request carries only
    ``status`` ({old, new}); config changes cannot be mixed into it.
    """
    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以提交 CICD 任务申请")
    if request_type != "modify":
        raise RuntimeError("新建 App 请使用「新增 App」申请")
    if source != "app_workbench":
        raise RuntimeError("CICD 配置修改请从 App 工作台的 CICD tab 提交")
    _validate_payload_fields(payload)
    app = apps_repo.get_app(conn, task_id) if task_id else None
    if not app:
        raise RuntimeError("CICD 修改申请必须关联有效 App")
    app_id = app["id"]
    catalog_service.require_app_editor(app, submitter, submitter_role)
    unsupported = set(payload) - _MUTABLE_FIELDS
    if unsupported:
        raise RuntimeError(f"App 工作台 CICD tab 只能修改 CICD 配置字段；不支持：{'、'.join(sorted(unsupported))}")
    if not payload:
        raise RuntimeError("CICD 配置没有任何字段发生变化")
    is_status = "status" in payload
    if is_status:
        if set(payload) != {"status"}:
            raise RuntimeError("启动/停止 CICD 需单独提交，不能与配置修改一起提交")
        change = payload["status"]
        target = str((change or {}).get("new") or "").strip() if isinstance(change, dict) else ""
        if target not in CICD_STATUSES:
            raise ValueError("CICD 状态只能是 running 或 stopped")
        if target == app["cicd_status"]:
            raise RuntimeError(f"该 App 的 CICD 已是 {target}")
        payload = {"status": {"old": app["cicd_status"], "new": target}}
    _validate_modify_repo_input(conn, app_id, payload)
    ts = beijing_timestamp()
    with transaction(conn):
        ensure_can_open_modify_request(conn, app_id, status_request=is_status)
        if is_status and payload["status"]["new"] == CICD_STOPPED:
            from app.services import cycle_service

            signed_up = cycle_service.has_open_signup(conn, app_id)
            if signed_up:
                raise RuntimeError(
                    f"该 App 已报名未发布的版本（{'、'.join(signed_up)}），要停止 CICD 请先撤回报名"
                )
        replaceable = [] if is_status else _replaceable_pending_modifies(conn, app_id)
        if replaceable and not replace_open:
            ids = "、".join(f"#{row['id']}" for row in replaceable)
            raise RuntimeError(f"该 app 已有待审批 CICD 修改申请 {ids}；继续提交将取消旧申请并只保留当前改动，请确认后重试")
        req_id = cicd_repo.insert_request(
            conn,
            task_id=app_id,
            app_id=app_id,
            request_type="modify",
            payload=payload,
            submitter=submitter,
            submitter_display=submitter_display,
            submitted_at=ts,
        )
        cancelled = []
        for old in replaceable:
            cancelled.append(int(old["id"]))
            conn.execute(
                "UPDATE cicd_task_requests SET status='cancelled', reviewer=?, reviewed_at=?, review_note=? WHERE id=?",
                (submitter, ts, f"被新的 CICD 修改申请 #{req_id} 替代", old["id"]),
            )
    result = _request_out(conn, req_id)
    if cancelled:
        result["cancelled_request_ids"] = cancelled
    return result


def submit_new_app(
    conn: sqlite3.Connection,
    *,
    official_name: str,
    repo_type: str,
    repo_name: str,
    branch: str,
    doc_target: str,
    app_type: str,
    config: dict,
    submitter: str,
    submitter_role: str,
    submitter_display: str = "",
) -> dict:
    """Submit a create request.  The App itself appears when it takes effect."""
    from app.identity import repo_to_git_identity

    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以申请新增 App")
    official_name = (official_name or "").strip()
    if not official_name:
        raise ValueError("必须提供 app 名称（official_name）")
    repo_type = cicd_config_domain.normalize_repo_type(repo_type)
    repo_name = (repo_name or "").strip().lstrip("/")
    branch = (branch or "").strip()
    if not branch:
        raise ValueError("branch 不能为空，请检查 repo_name / branch")
    validate_repo_path_input(repo_type, repo_name)
    git_url, git_branch = repo_to_git_identity(repo_type, repo_name, branch, refresh_manifest=repo_name.endswith(".xml"))
    if not git_url or not git_branch:
        raise ValueError("无法解析 repo 身份（repo_name 为空或 .xml manifest 解析失败），请检查 repo_name / branch")
    community = cicd_config_domain.normalize_community_artifacts(
        config.get("cicd_community_artifact") or config.get("community_artifact")
    )
    payload = {
        "app_name": official_name,
        "repo_type": repo_type,
        "repo_name": repo_name,
        "branch": branch,
        "doc_target": normalize_doc_target(doc_target),
        "app_type": (app_type or "").strip(),
        "app_version": str(config.get("app_version") or ""),
        "build_product": config.get("build_product") or [],
        "community_artifact": community,
        "build_image": str(config.get("cicd_build_image") or config.get("build_image") or "").strip(),
        "test_timeout": cicd_config_domain.normalize_test_timeout(
            config.get("cicd_test_timeout") or config.get("test_timeout")
        ),
        "owner_username": submitter,
        "status": CICD_RUNNING,
        "notes": str(config.get("cicd_notes") or config.get("notes") or "").strip(),
    }
    ts = beijing_timestamp()
    with transaction(conn):
        for url, br in ((repo_name, branch), (git_url, git_branch)):
            existing = _find_app_by_identity(conn, url, br)
            if existing:
                raise RuntimeError(f"该 Gerrit URL + branch 已存在 app（{existing['id']}），请直接修改已有 app，不能重复创建")
        open_create = _open_create_for_identity(conn, repo_name, branch)
        if open_create:
            raise RuntimeError(f"该 Gerrit URL + branch 已有未完成的新增 App 申请 #{open_create['id']}")
        req_id = cicd_repo.insert_request(
            conn,
            task_id=None,
            app_id=None,
            request_type="create",
            payload=payload,
            submitter=submitter,
            submitter_display=submitter_display,
            submitted_at=ts,
        )
    return {"ok": True, "git_url": git_url, "git_branch": git_branch, "request": _request_out(conn, req_id)}


# ---------------------------------------------------------------------------
# Review and delivery
# ---------------------------------------------------------------------------


def approve_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    reviewer: str,
    reviewer_role: str,
    review_note: str = "",
    approval_mode: str = "immediate",
    jira_id: str = "",
    jira_auto_created: int = 0,
) -> dict:
    """Approve: apply now (``immediate``) or after SPD delivery (``dispatch_spd``)."""
    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以审批 CICD 任务申请")
    req = _get_request(conn, req_id)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，无法审批")
    is_self = 1 if reviewer == req["submitter"] else 0
    ts = beijing_timestamp()
    with transaction(conn):
        if approval_mode == "dispatch_spd":
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='dispatch_spd', delivery_status='pending',
                       jira_id=?, jira_auto_created=?, is_self_approved=?
                   WHERE id=?""",
                (reviewer, ts, review_note, jira_id, jira_auto_created, is_self, req_id),
            )
        else:
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='immediate', is_self_approved=?
                   WHERE id=?""",
                (reviewer, ts, review_note, is_self, req_id),
            )
            _apply_request(conn, req, user=reviewer, role=reviewer_role, ts=ts)
    return _request_out(conn, req_id)


def reject_request(conn: sqlite3.Connection, req_id: int, *, reviewer: str, reviewer_role: str, review_note: str) -> dict:
    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以拒绝 CICD 任务申请")
    if not (review_note or "").strip():
        raise ValueError("拒绝必须填写理由")
    req = _get_request(conn, req_id)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，无法拒绝")
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_task_requests SET status='rejected', reviewer=?, reviewed_at=?, review_note=? WHERE id=?",
            (reviewer, beijing_timestamp(), review_note.strip(), req_id),
        )
    return _request_out(conn, req_id)


def cancel_request(conn: sqlite3.Connection, req_id: int, *, username: str, role: str) -> dict:
    req = _get_request(conn, req_id)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，只有 pending 状态可以取消")
    if req["submitter"] != username and role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有提交人或 RM 可以取消申请")
    with transaction(conn):
        conn.execute("UPDATE cicd_task_requests SET status='cancelled', reviewed_at=? WHERE id=?",
                     (beijing_timestamp(), req_id))
    return _request_out(conn, req_id)


def mark_visited(conn: sqlite3.Connection, username: str) -> None:
    with transaction(conn):
        cicd_repo.mark_notification_visited(conn, username, beijing_timestamp())


def deliver_request(conn: sqlite3.Connection, req_id: int, *, deliverer: str, deliverer_role: str) -> dict:
    if deliverer_role not in CICD_DELIVER_ROLES:
        raise PermissionError("只有 SPD、RM 可以标记已交付")
    req = _get_request(conn, req_id)
    if req.get("delivery_status") not in ("pending", "returned"):
        raise RuntimeError(f"该申请的交付状态为 '{req.get('delivery_status')}'，无法标记已交付")
    ts = beijing_timestamp()
    with transaction(conn):
        _apply_request(conn, req, user=deliverer, role=deliverer_role, ts=ts)
        conn.execute(
            "UPDATE cicd_task_requests SET delivery_status='delivered', delivered_by=?, delivered_at=? WHERE id=?",
            (deliverer, ts, req_id),
        )
    return _request_out(conn, req_id)


def return_delivery(conn: sqlite3.Connection, req_id: int, *, returner: str, returner_role: str, reason: str) -> dict:
    if returner_role not in CICD_RETURN_ROLES:
        raise PermissionError("只有 SPD 可以退回交付申请")
    if not (reason or "").strip():
        raise ValueError("退回必须填写原因")
    req = _get_request(conn, req_id)
    if req.get("delivery_status") != "pending":
        raise RuntimeError(f"该申请的交付状态为 '{req.get('delivery_status')}'，无法退回")
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_task_requests SET delivery_status='returned', returned_reason=?, returned_at=? WHERE id=?",
            (reason.strip(), beijing_timestamp(), req_id),
        )
    return _request_out(conn, req_id)


def reject_returned_request(
    conn: sqlite3.Connection, req_id: int, *, reviewer: str, reviewer_role: str, review_note: str
) -> dict:
    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以拒绝已退回的交付申请")
    if not (review_note or "").strip():
        raise ValueError("拒绝必须填写理由")
    req = _get_request(conn, req_id)
    if req.get("status") != "approved" or req.get("delivery_status") != "returned":
        raise RuntimeError("只有已退回的待交付申请可以拒绝")
    with transaction(conn):
        conn.execute(
            """UPDATE cicd_task_requests
               SET status='rejected', reviewer=?, reviewed_at=?, review_note=?, delivery_status=''
               WHERE id=?""",
            (reviewer, beijing_timestamp(), review_note.strip(), req_id),
        )
    return _request_out(conn, req_id)


def re_dispatch_request(conn: sqlite3.Connection, req_id: int, *, actor: str, actor_role: str) -> dict:
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以重新下发")
    req = _get_request(conn, req_id)
    if req.get("delivery_status") != "returned":
        raise RuntimeError(f"该申请的交付状态为 '{req.get('delivery_status')}'，只有 returned 状态可以重新下发")
    with transaction(conn):
        conn.execute("UPDATE cicd_task_requests SET delivery_status='pending' WHERE id=?", (req_id,))
    return _request_out(conn, req_id)


def apply_returned_request(conn: sqlite3.Connection, req_id: int, *, actor: str, actor_role: str) -> dict:
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以直接生效")
    req = _get_request(conn, req_id)
    if req.get("delivery_status") not in ("pending", "returned"):
        raise RuntimeError(f"该申请的交付状态为 '{req.get('delivery_status')}'，无法直接生效")
    ts = beijing_timestamp()
    with transaction(conn):
        _apply_request(conn, req, user=actor, role=actor_role, ts=ts)
        conn.execute(
            """UPDATE cicd_task_requests
               SET delivery_status='delivered', delivered_by=?, delivered_at=?, approval_mode='immediate'
               WHERE id=?""",
            (actor, ts, req_id),
        )
    return _request_out(conn, req_id)


# ---------------------------------------------------------------------------
# app_info preview for the new-App form (no DB writes)
# ---------------------------------------------------------------------------


def preview_app_info(
    conn: sqlite3.Connection,
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter_role: str,
    _fetch_fn=None,
) -> dict:
    """Resolve identity, reject duplicates, and fetch app_info for preview.

    Gerrit failures are returned as soft flags so the form can still submit.
    """
    from app.identity import repo_to_git_identity

    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以预览 Gerrit app_info")
    repo_name = (repo_name or "").strip().lstrip("/")
    branch = (branch or "").strip()
    if not repo_name:
        raise ValueError("repo_name 不能为空，请检查 repo_name / branch")
    if not branch:
        raise ValueError("branch 不能为空，请检查 repo_name / branch")
    repo_type = cicd_config_domain.normalize_repo_type(repo_type)
    validate_repo_path_input(repo_type, repo_name)
    existing = _find_app_by_identity(conn, repo_name, branch)
    if existing:
        raise RuntimeError(f"该 Gerrit URL + branch 已存在 app（{existing['id']}），请直接修改已有 app，不能重复创建")
    is_manifest = repo_name.endswith(".xml")
    git_url, git_branch = repo_to_git_identity(repo_type, repo_name, branch, refresh_manifest=is_manifest)
    base: dict = {"git_url": git_url, "git_branch": git_branch, "needs_network": is_manifest}
    if not git_url or not git_branch:
        return {**base, "app_info_unavailable": True,
                "app_info_error": f"manifest 路径需要联网解析（Gerrit 不可达），无法确定 repo 身份 ({repo_name})"}
    existing = _find_app_by_identity(conn, git_url, git_branch)
    if existing:
        raise RuntimeError(f"该 Gerrit URL + branch 已存在 app（{existing['id']}），请直接修改已有 app，不能重复创建")
    if _fetch_fn is None:
        from app.integrations.gerrit import fetch_app_info as _fetch_fn
    try:
        raw_json, commit_id = _fetch_fn(git_url, git_branch, project_root=settings.db_path.parent)
        parsed = app_info_domain.parse_app_info(raw_json)
    except Exception as exc:  # noqa: BLE001 - surfaced as a soft flag
        return {**base, "app_info_unavailable": True, "app_info_error": str(exc)}
    community = parsed.get("community_artifacts", {})
    return {
        **base,
        "app_info_unavailable": False,
        "app_info_error": None,
        "app_version": parsed.get("app_version", ""),
        "x86_chips": ",".join(order_chips(parsed.get("x86_chips", []))),
        "arm_chips": ",".join(order_chips(parsed.get("arm_chips", []))),
        "python_label": ",".join(parsed.get("python_labels", [])),
        "pytorch_label": ",".join(parsed.get("pytorch_labels", [])),
        "os": ",".join(parsed.get("build_os", [])),
        "arch": ",".join(parsed.get("build_arches", [])),
        "community_release_status": community.get("release_status", ""),
        "community_artifact": community.get("artifact_types", []),
        "dependencies": app_info_domain.declared_dependencies(parsed.get("raw") or {}),
        "commit_id": commit_id,
    }
