"""CICD router — task and request management.

Faithful port of server.py GET/POST /api/cicd/* handlers.

Phase-4 stubs NOT wired:
  POST /api/cicd/apps/new      — cicd_first_new_app
  POST /api/cicd/tasks/abandon — abandon_task

Paths match server.py exactly (do_GET:447-499, do_POST:1021-1199).
GET handlers are plain `def` (thread pool — no blocking concern).
POST handlers are `async def` to read the JSON body via Request.json().
"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, Query, Request

from app.api.errors import AuthzError
from app.deps import get_db, require_login
from app.integrations import jira as jira_integration
from app.services import cicd_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cicd", tags=["cicd"])


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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """GET /api/cicd/deliveries — delivery workflow requests.

    Mirrors server.py:do_GET:485-499.
    """
    role = user["role"]
    if role not in {"SPD", "RM", "Owner"}:
        raise AuthzError("无权访问交付列表")
    submitter_filter = user["username"] if role == "Owner" else None
    deliveries = cicd_service.list_deliveries(
        conn,
        status_filter=status,
        role=role,
        submitter=submitter_filter,
    )
    return {"deliveries": deliveries}


# ---------------------------------------------------------------------------
# POST endpoints — requests (async def to read JSON body)
# ---------------------------------------------------------------------------


@router.post("/requests/submit")
async def post_submit(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/submit — submit a create/modify request.

    Mirrors server.py:do_POST:1021-1037.
    """
    body: dict = await request.json()
    role = user["role"]
    if role not in cicd_service.CICD_CREATE_ROLES:
        raise AuthzError("只有 Owner、RM 可以提交 CICD 任务申请")
    req = cicd_service.submit_request(
        conn,
        task_id=body.get("task_id") or None,
        request_type=body.get("request_type", "create"),
        payload=body.get("payload", {}),
        submitter=user["username"],
        submitter_role=role,
        submitter_display=user.get("display_name", ""),
    )
    return {"ok": True, "request": req}


@router.post("/requests/approve")
async def post_approve(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/approve — RM approves a pending request (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1038-1089.
    Jira auto-create: attempted before the approval transaction, failure is
    logged and does NOT block approval.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以审批")
    approval_mode = body.get("approval_mode", "immediate")
    jira_auto_created = int(body.get("jira_auto_created", 0))
    jira_id = body.get("jira_id", "")

    # Auto-create Jira issue when dispatching to SPD (mirrors server.py:1048-1076)
    if jira_auto_created and approval_mode == "dispatch_spd" and not jira_id:
        try:
            jcfg = jira_integration.load_config()
            if jcfg:
                row = conn.execute(
                    "SELECT request_type, task_id, payload, submitter "
                    "FROM cicd_task_requests WHERE id=?",
                    (int(body["request_id"]),),
                ).fetchone()
                if row:
                    import json as _json

                    payload_dict = _json.loads(row["payload"] or "{}")
                    title = jira_integration.compute_title(
                        conn,
                        row["request_type"],
                        payload_dict,
                        row["task_id"],
                    )
                    desc = jira_integration.build_description(
                        request_id=int(body["request_id"]),
                        request_type=row["request_type"],
                        payload=payload_dict,
                        task_id=row["task_id"],
                        submitter=row["submitter"],
                        title=title,
                        review_note=body.get("review_note", ""),
                    )
                    jira_id = jira_integration.create_issue(
                        title, desc, jira_config=jcfg
                    )
        except Exception as je:
            logger.warning("Jira auto-create failed: %s", je)
            # Do not block approval on Jira failure

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
    return {"ok": True, "request": req}


@router.post("/requests/reject")
async def post_reject(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/reject — RM rejects a pending request (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1090-1103.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以拒绝")
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
    user: dict = Depends(require_login),
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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/deliver — SPD/RM marks as delivered (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1147-1159.
    """
    body: dict = await request.json()
    if user["role"] not in {"SPD", "RM"}:
        raise AuthzError("只有 SPD、RM 可以标记已交付")
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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/return-delivery — SPD returns to RM.

    Mirrors server.py:do_POST:1160-1173.
    """
    body: dict = await request.json()
    if user["role"] != "SPD":
        raise AuthzError("只有 SPD 可以退回交付申请")
    req = cicd_service.return_delivery(
        conn,
        int(body["request_id"]),
        returner=user["username"],
        returner_role=user["role"],
        reason=body.get("reason", ""),
    )
    return {"ok": True, "request": req}


@router.post("/requests/re-dispatch")
async def post_re_dispatch(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/re-dispatch — RM re-dispatches returned delivery (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1174-1186.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以重新下发")
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
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/requests/apply-returned — RM applies returned request immediately (Ruling C: Admin excluded).

    Mirrors server.py:do_POST:1187-1199.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以直接生效")
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


@router.post("/tasks/transfer-owner")
async def post_transfer_owner(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/tasks/transfer-owner — direct owner transfer (RM only; Ruling C).

    Mirrors server.py:do_POST:1115-1128.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以直接修改负责人")
    task = cicd_service.transfer_owner(
        conn,
        body["task_id"],
        body["new_owner"],
        actor=user["username"],
        actor_role=user["role"],
    )
    return {"ok": True, "task": task}


@router.post("/tasks/abandon")
async def post_abandon_task(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/tasks/abandon — RM retires a Stopped task to Abandoned (Ruling A).

    Direct action (no pending queue) — mirrors transfer_owner semantics.
    Only Stopped tasks can be abandoned; Abandoned is terminal.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以废弃 CICD 任务")
    task = cicd_service.abandon_task(
        conn,
        body["task_id"],
        reviewer=user["username"],
        reviewer_role=user["role"],
    )
    return {"ok": True, "task": task}


@router.post("/tasks/delete")
async def post_delete_task(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """POST /api/cicd/tasks/delete — delete an Abandoned task (RM only; Ruling C).

    Mirrors server.py:do_POST:1129-1141.
    """
    body: dict = await request.json()
    if user["role"] not in cicd_service.CICD_APPROVER_ROLES:
        raise AuthzError("只有 RM 可以删除 CICD 任务")
    cicd_service.delete_task(
        conn,
        body["task_id"],
        actor=user["username"],
        actor_role=user["role"],
    )
    return {"ok": True}


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
