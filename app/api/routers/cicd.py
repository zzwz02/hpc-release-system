"""CICD router — task and request management.

Faithful port of server.py GET/POST /api/cicd/* handlers.

New Apps:
  POST /api/cicd/apps/fetch-preview — identity + app_info preview for the new-App form
  POST /api/cicd/apps/new           — submit a create request

Paths match server.py exactly (do_GET:447-499, do_POST:1021-1199).
GET handlers are plain `def` (thread pool — no blocking concern).
POST handlers are `async def` to read the JSON body via Request.json().
"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, Query, Request

from app.deps import get_db, require_capability, require_login
from app.domain.access_actions import cicd_request_allowed_actions
from app.domain.cicd_config import CICD_REPO_TYPE_DEFAULT
from app.domain.cicd_requests import lifecycle_fields
from app.domain.permissions import has_capability
from app.integrations import jira as jira_integration
from app.services import cicd_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cicd", tags=["cicd"])

require_cicd_submit = require_capability(
    "cicd.request.submit",
    message="只有 Owner、RM 可以提交 CICD 任务申请",
)
require_cicd_fetch_preview = require_capability(
    "cicd.request.submit",
    message="只有 Owner、RM 可以预览 Gerrit app_info",
)
require_cicd_first_create = require_capability(
    "cicd.request.submit",
    message="只有 Owner、RM 可以发起 CICD-first 建 app",
)
require_cicd_approve = require_capability(
    "cicd.request.approve",
    message="只有 RM 可以审批",
)
require_cicd_reject = require_capability(
    "cicd.request.reject",
    message="只有 RM 可以拒绝",
)
require_cicd_cancel = require_capability(
    "cicd.request.cancel",
    message="只有提交人或 RM 可以取消申请",
)
require_delivery_view = require_capability(
    "cicd.delivery.view",
    message="无权访问交付列表",
)
require_delivery_confirm = require_capability(
    "cicd.delivery.confirm",
    message="只有 SPD、RM 可以标记已交付",
)
require_delivery_return = require_capability(
    "cicd.delivery.return",
    message="只有 SPD 可以退回交付申请",
)
require_delivery_redispatch = require_capability(
    "cicd.delivery.redispatch",
    message="只有 RM 可以重新下发",
)
require_delivery_apply_returned = require_capability(
    "cicd.delivery.apply_returned",
    message="只有 RM 可以直接生效",
)
require_delivery_reject_returned = require_capability(
    "cicd.delivery.reject_returned",
    message="只有 RM 可以拒绝",
)


def _payload_context_value(payload: dict, key: str) -> str:
    value = payload.get(key)
    if isinstance(value, dict):
        value = value.get("old") or value.get("new") or ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "").strip()


def _jira_task_context(
    conn: sqlite3.Connection,
    *,
    request_type: str,
    task_id: str | None,
    app_id: str | None,
    payload: dict,
) -> dict[str, str]:
    context = {
        key: _payload_context_value(payload, key)
        for key in ("app_name", "app_version", "repo_type", "repo_name", "branch")
    }
    app_ref = app_id or task_id or str(payload.get("app_id") or "").strip()
    if app_ref:
        task = next(
            (
                item
                for item in cicd_service.list_tasks(conn)
                if item.get("id") == app_ref or item.get("app_id") == app_ref
            ),
            None,
        )
        if task:
            task_context = {
                "app_name": str(task.get("app_name") or ""),
                "app_version": str(task.get("app_version") or ""),
                "repo_type": str(task.get("repo_type") or ""),
                "repo_name": str(task.get("repo_name") or ""),
                "branch": str(task.get("branch") or ""),
            }
            if request_type == "create":
                context = {**task_context, **{k: v for k, v in context.items() if v}}
            else:
                context = {**context, **{k: v for k, v in task_context.items() if v}}
    return {key: value for key, value in context.items() if value}


# ---------------------------------------------------------------------------
# GET endpoints — plain `def` (thread pool)
# ---------------------------------------------------------------------------


@router.get("/tasks")
def get_tasks(
    status: str | None = Query(default=None),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/tasks — list all CICD tasks, optionally filtered by status.

    Mirrors server.py:do_GET:447-452.
    """
    tasks = cicd_service.list_tasks(conn, status_filter=status)
    return {"tasks": tasks}


@router.get("/config")
def get_config(user: dict = Depends(require_login)) -> dict:
    """Return browser-safe CICD integration configuration."""
    return {"jira_browse_url": jira_integration.browse_url()}


@router.get("/tasks/{task_id}/history")
def get_task_history(
    task_id: str,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/tasks/<id>/history — approved request history for a task.

    Mirrors server.py:do_GET:453-458.
    """
    history = cicd_service.get_task_history(conn, task_id)
    return {"history": history}


@router.get("/requests")
def get_requests(
    only_mine: str = Query(default=""),
    task_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since_days: str | None = Query(default=None),
    include_allowed_actions: bool = Query(default=False),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/requests — list requests with filters.

    Mirrors server.py:do_GET:459-478.
    """
    role = user["role"]
    username = user["username"]
    is_only_mine = only_mine == "1"
    days = int(since_days) if since_days else None
    requests = cicd_service.list_requests(
        conn,
        username=username if is_only_mine else None,
        role=role,
        task_id=task_id,
        status_filter=status,
        since_days=days,
        exclude_cancelled=True,
    )
    if include_allowed_actions:
        for item in requests:
            item.update(lifecycle_fields(item))
            item["allowed_actions"] = cicd_request_allowed_actions(
                item,
                role=role,
                username=username,
            )
    return {"requests": requests}


@router.get("/notifications")
def get_notifications(
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/notifications — notification badge counts.

    Mirrors server.py:do_GET:480-483.
    """
    return cicd_service.get_notifications(conn, user["username"], user["role"])


@router.get("/deliveries")
def get_deliveries(
    status: str | None = Query(default=None),
    include_allowed_actions: bool = Query(default=False),
    user: dict = Depends(require_delivery_view),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/deliveries — delivery workflow requests.

    Mirrors server.py:do_GET:485-499.
    """
    role = user["role"]
    submitter_filter = (
        user["username"]
        if has_capability(role, "cicd.delivery.view.owned")
        and not has_capability(role, "cicd.delivery.view.all")
        else None
    )
    deliveries = cicd_service.list_deliveries(
        conn,
        status_filter=status,
        role=role,
        submitter=submitter_filter,
    )
    if include_allowed_actions:
        for item in deliveries:
            item.update(lifecycle_fields(item))
            item["allowed_actions"] = cicd_request_allowed_actions(
                item,
                role=role,
                username=user["username"],
            )
    return {"deliveries": deliveries}


# ---------------------------------------------------------------------------
# POST endpoints — requests (async def to read JSON body)
# ---------------------------------------------------------------------------


@router.post("/requests/submit")
async def post_submit(
    request: Request,
    user: dict = Depends(require_cicd_submit),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/submit — submit a create/modify request.

    Mirrors server.py:do_POST:1021-1037.
    """
    body: dict = await request.json()
    role = user["role"]
    req = cicd_service.submit_request(
        conn,
        task_id=body.get("task_id") or None,
        request_type=body.get("request_type", "create"),
        payload=body.get("payload", {}),
        submitter=user["username"],
        submitter_role=role,
        submitter_display=user.get("display_name", ""),
        source=body.get("source", "cicd_workbench"),
        replace_open=bool(body.get("replace_open")),
    )
    return {"ok": True, "request": req}


@router.post("/requests/approve")
async def post_approve(
    request: Request,
    user: dict = Depends(require_cicd_approve),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/approve — RM approves a pending request (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1038-1089.
    Jira auto-create: attempted before the approval transaction, failure is
    logged and does NOT block approval.
    """
    body: dict = await request.json()
    approval_mode = body.get("approval_mode", "immediate")
    jira_auto_created = int(body.get("jira_auto_created", 0))
    jira_id = body.get("jira_id", "")

    # Auto-create Jira issue when dispatching to SPD (mirrors server.py:1048-1076)
    jira_error = ""
    if jira_auto_created and approval_mode == "dispatch_spd" and not jira_id:
        try:
            jcfg = jira_integration.load_config()
            if not jcfg:
                jira_error = "未配置 JIRA：release_system.conf 的 [jira] 需要 JIRA_BASE_URL 和 JIRA_TOKEN"
            else:
                row = conn.execute(
                    "SELECT request_type, task_id, app_id, payload, submitter "
                    "FROM cicd_task_requests WHERE id=?",
                    (int(body["request_id"]),),
                ).fetchone()
                if row:
                    import json as _json

                    payload_dict = _json.loads(row["payload"] or "{}")
                    task_context = _jira_task_context(
                        conn,
                        request_type=row["request_type"],
                        task_id=row["task_id"],
                        app_id=row["app_id"],
                        payload=payload_dict,
                    )
                    title = jira_integration.compute_title(
                        conn,
                        row["request_type"],
                        payload_dict,
                        row["task_id"],
                        task_context=task_context,
                    )
                    desc = jira_integration.build_description(
                        request_id=int(body["request_id"]),
                        request_type=row["request_type"],
                        payload=payload_dict,
                        task_id=row["task_id"],
                        submitter=row["submitter"],
                        title=title,
                        review_note=body.get("review_note", ""),
                        task_context=task_context,
                    )
                    jira_id = jira_integration.create_issue(
                        title, desc, jira_config=jcfg
                    )
        except Exception as je:
            logger.warning("Jira auto-create failed: %s", je)
            # Do not block approval on Jira failure; tell the reviewer instead.
            jira_error = str(je) or je.__class__.__name__

    req = cicd_service.approve_request(
        conn,
        int(body["request_id"]),
        reviewer=user["username"],
        reviewer_role=user["role"],
        review_note=body.get("review_note", ""),
        approval_mode=approval_mode,
        jira_id=jira_id,
        jira_auto_created=jira_auto_created,
    )
    response = {"ok": True, "request": req}
    if jira_error:
        response["jira_error"] = jira_error
    return response


@router.post("/requests/reject")
async def post_reject(
    request: Request,
    user: dict = Depends(require_cicd_reject),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/reject — RM rejects a pending request (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1090-1103.
    """
    body: dict = await request.json()
    req = cicd_service.reject_request(
        conn,
        int(body["request_id"]),
        reviewer=user["username"],
        reviewer_role=user["role"],
        review_note=body.get("review_note", ""),
    )
    return {"ok": True, "request": req}


@router.post("/requests/cancel")
async def post_cancel(
    request: Request,
    user: dict = Depends(require_cicd_cancel),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/cancel — cancel a pending request.

    Mirrors server.py:do_POST:1104-1114.
    """
    body: dict = await request.json()
    req = cicd_service.cancel_request(
        conn,
        int(body["request_id"]),
        username=user["username"],
        role=user["role"],
    )
    return {"ok": True, "request": req}


@router.post("/requests/deliver")
async def post_deliver(
    request: Request,
    user: dict = Depends(require_delivery_confirm),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/deliver — SPD/RM marks as delivered (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1147-1159.
    """
    body: dict = await request.json()
    req = cicd_service.deliver_request(
        conn,
        int(body["request_id"]),
        deliverer=user["username"],
        deliverer_role=user["role"],
    )
    return {"ok": True, "request": req}


@router.post("/requests/return-delivery")
async def post_return_delivery(
    request: Request,
    user: dict = Depends(require_delivery_return),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/return-delivery — SPD returns to RM.

    Mirrors server.py:do_POST:1160-1173.
    """
    body: dict = await request.json()
    req = cicd_service.return_delivery(
        conn,
        int(body["request_id"]),
        returner=user["username"],
        returner_role=user["role"],
        reason=body.get("reason", ""),
    )
    return {"ok": True, "request": req}


@router.post("/requests/reject-returned")
async def post_reject_returned(
    request: Request,
    user: dict = Depends(require_delivery_reject_returned),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/reject-returned — RM rejects a returned delivery."""
    body: dict = await request.json()
    req = cicd_service.reject_returned_request(
        conn,
        int(body["request_id"]),
        reviewer=user["username"],
        reviewer_role=user["role"],
        review_note=body.get("review_note", ""),
    )
    return {"ok": True, "request": req}


@router.post("/requests/re-dispatch")
async def post_re_dispatch(
    request: Request,
    user: dict = Depends(require_delivery_redispatch),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/re-dispatch — RM re-dispatches returned delivery (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1174-1186.
    """
    body: dict = await request.json()
    req = cicd_service.re_dispatch_request(
        conn,
        int(body["request_id"]),
        actor=user["username"],
        actor_role=user["role"],
    )
    return {"ok": True, "request": req}


@router.post("/requests/apply-returned")
async def post_apply_returned(
    request: Request,
    user: dict = Depends(require_delivery_apply_returned),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/apply-returned — RM applies returned request immediately (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1187-1199.
    """
    body: dict = await request.json()
    req = cicd_service.apply_returned_request(
        conn,
        int(body["request_id"]),
        actor=user["username"],
        actor_role=user["role"],
    )
    return {"ok": True, "request": req}


# ---------------------------------------------------------------------------
# POST endpoints — tasks
# ---------------------------------------------------------------------------


@router.post("/apps/fetch-preview")
async def post_cicd_apps_fetch_preview(
    request: Request,
    user: dict = Depends(require_cicd_fetch_preview),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Resolve the repo identity, reject duplicates and preview app_info (no writes)."""
    body: dict = await request.json()
    return cicd_service.preview_app_info(
        conn,
        repo_type=body.get("repo_type", CICD_REPO_TYPE_DEFAULT),
        repo_name=body.get("repo_name", ""),
        branch=body.get("branch", ""),
        submitter_role=user["role"],
    )


@router.post("/apps/new")
async def post_cicd_apps_new(
    request: Request,
    user: dict = Depends(require_cicd_first_create),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Submit a request for a new App.  The App is created when it takes effect.

    Body: official_name, doc_target (manual|ai4sci), app_type, repo_type,
    repo_name, branch, and optional CICD config (cicd_build_image,
    cicd_test_timeout, cicd_community_artifact, cicd_notes, app_version).
    """
    body: dict = await request.json()
    from app.domain.app_profile import DOC_TARGETS

    if not isinstance(body.get("doc_target"), str) or body["doc_target"] not in DOC_TARGETS:
        raise ValueError("请选择类型（HPC/AI4Sci）")
    if not isinstance(body.get("app_type"), str) or not body["app_type"].strip():
        raise ValueError("请填写 APP 类型")
    return cicd_service.submit_new_app(
        conn,
        official_name=body.get("official_name", ""),
        repo_type=body.get("repo_type", CICD_REPO_TYPE_DEFAULT),
        repo_name=body.get("repo_name", ""),
        branch=body.get("branch", ""),
        doc_target=body["doc_target"],
        app_type=body["app_type"],
        config=body,
        submitter=user["username"],
        submitter_role=user["role"],
        submitter_display=user.get("display_name", ""),
    )


# ---------------------------------------------------------------------------
# POST endpoints — notifications
# ---------------------------------------------------------------------------


@router.post("/notifications/mark-visited")
async def post_mark_visited(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/notifications/mark-visited — reset notification badge.

    Mirrors server.py:do_POST:1142-1146.
    """
    cicd_service.mark_visited(conn, user["username"])
    return {"ok": True}
