"""CICD service — task and request management.

Faithful port of release_system/core.py:3639-4364.

Phase-4 stubs (NOT implemented yet — raise NotImplementedError):
  - sync_decision_to_cicd
  - abandon_task
  - cicd_first_new_app
"""
from __future__ import annotations

import sqlite3

from app.db.connection import transaction
from app.repositories import cicd_repo
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Role constants — mirrors core.py:3639-3640
# ---------------------------------------------------------------------------
CICD_APPROVER_ROLES: frozenset[str] = frozenset({"RM", "Admin"})
CICD_CREATE_ROLES: frozenset[str] = frozenset({"Owner", "RM", "Admin"})
CICD_STATUSES: frozenset[str] = frozenset({"Running", "Stopped", "Abandoned"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_payload_fields(payload: dict) -> None:
    """Raise RuntimeError for any unsupported CICD payload field.

    Mirrors core.py:_validate_cicd_payload_fields.  status is allowed in
    core.py's CICD_TASK_FIELDS (it just isn't in the repo mutable set), so
    we validate against the same set core.py uses.
    """
    _ALLOWED = frozenset(
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
        }
    )
    for field in payload or {}:
        if field not in _ALLOWED:
            raise RuntimeError(f"不支持的 CICD 字段：{field}")


def _attach_task_info(
    conn: sqlite3.Connection,
    items: list[dict],
) -> None:
    """Attach task display fields to a list of request dicts.

    Mirrors core.py:_attach_cicd_request_task_info.
    """
    task_ids = list({d["task_id"] for d in items if d.get("task_id")})
    task_map = cicd_repo.task_mini_info(conn, task_ids)
    for d in items:
        t = task_map.get(d.get("task_id") or "", {})
        if not t and d.get("request_type") == "create":
            p = d.get("payload") or {}
            d["task_app_name"] = p.get("app_name", "")
            d["task_app_version"] = p.get("app_version", "")
            d["task_repo_name"] = p.get("repo_name", "")
            d["task_branch"] = p.get("branch", "")
            d["task_status"] = p.get("status", "Running")
        else:
            d["task_app_name"] = t.get("app_name", "")
            d["task_app_version"] = t.get("app_version", "")
            d["task_repo_name"] = t.get("repo_name", "")
            d["task_branch"] = t.get("branch", "")
            d["task_status"] = t.get("status", "")


def _attach_owner_display(
    conn: sqlite3.Connection,
    tasks: list[dict],
) -> None:
    """Attach owner_display to task dicts.

    Mirrors core.py:_attach_owner_display.
    """
    if not tasks:
        return
    usernames = list({t["owner_username"] for t in tasks})
    rows = conn.execute(
        "SELECT username, display_name FROM users WHERE username IN ({})".format(
            ",".join("?" * len(usernames))
        ),
        usernames,
    ).fetchall()
    display_map = {r["username"]: r["display_name"] for r in rows}
    for t in tasks:
        u = t["owner_username"]
        dn = display_map.get(u, "")
        t["owner_display"] = dn if dn else u


# ---------------------------------------------------------------------------
# Response-shape helpers — strip columns the old server never exposed
# ---------------------------------------------------------------------------

# app_id was added in Phase 0 as a new column; core.py never selected it in
# list_cicd_tasks / get_cicd_task so it never appeared in golden responses.
_TASK_STRIP: frozenset[str] = frozenset({"app_id"})

# origin was added in Phase 0 as an internal audit column; core.py never
# selected it so it never appeared in any golden request responses.
_REQUEST_STRIP: frozenset[str] = frozenset({"origin"})


def _strip_task(t: dict) -> dict:
    """Remove Phase-0-only task columns that the old server never returned."""
    for k in _TASK_STRIP:
        t.pop(k, None)
    return t


def _strip_request(r: dict) -> dict:
    """Remove Phase-0-only request columns that the old server never returned."""
    for k in _REQUEST_STRIP:
        r.pop(k, None)
    return r


# ---------------------------------------------------------------------------
# Apply helpers — mirrors core.py:_apply_cicd_request
# ---------------------------------------------------------------------------


def _apply_cicd_request(
    conn: sqlite3.Connection,
    req_id: int,
    payload: dict,
    task_id: str | None,
    request_type: str,
    ts: str,
) -> str:
    """Create or update cicd_tasks after approval.  Returns the task_id."""
    _validate_payload_fields(payload)
    if request_type == "create":
        new_id = cicd_repo.next_cicd_id(conn)
        cicd_repo.create_task(
            conn,
            task_id=new_id,
            app_id=None,
            app_name=payload.get("app_name", ""),
            app_version=payload.get("app_version", ""),
            repo_type=payload.get("repo_type", "git"),
            repo_name=payload.get("repo_name", ""),
            branch=payload.get("branch", ""),
            build_product=payload.get("build_product", []),
            community_artifact=payload.get("community_artifact", []),
            build_image=payload.get("build_image", ""),
            test_timeout=int(payload.get("test_timeout", 40)),
            owner_username=payload.get("owner_username", ""),
            status=payload.get("status", "Running"),
            notes=payload.get("notes", ""),
            created_at=ts,
            updated_at=ts,
        )
        cicd_repo.set_request_task_id(conn, req_id, new_id)
        return new_id
    else:
        # modify: apply diff — payload is {field: {old, new}}
        if not task_id:
            raise RuntimeError("修改请求缺少 task_id")
        fields: dict = {}
        for field, change in payload.items():
            fields[field] = change.get("new")
        cicd_repo.apply_modify_fields(conn, task_id, fields, updated_at=ts)
        return task_id


# ---------------------------------------------------------------------------
# Read functions
# ---------------------------------------------------------------------------


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
) -> list[dict]:
    """Return all cicd_tasks with pending/delivery flags and owner display.

    Mirrors core.py:list_cicd_tasks.
    """
    if status_filter and status_filter in CICD_STATUSES:
        tasks = cicd_repo.list_tasks(conn, status_filter=status_filter)
    else:
        tasks = cicd_repo.list_tasks(conn)

    pending_ids = cicd_repo.pending_task_ids(conn)
    delivery_ids = cicd_repo.delivery_pending_task_ids(conn)
    for t in tasks:
        t["has_pending"] = t["id"] in pending_ids
        t["has_pending_delivery"] = t["id"] in delivery_ids

    _attach_owner_display(conn, tasks)
    return [_strip_task(t) for t in tasks]


def get_task_history(
    conn: sqlite3.Connection,
    task_id: str,
) -> list[dict]:
    """Return approved request history for a task.

    Mirrors core.py:get_cicd_task_history.
    """
    items = cicd_repo.task_history(conn, task_id)
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
    """Return cicd_task_requests with flexible filters.

    Mirrors core.py:list_cicd_requests.  since_days is converted to a
    pre-computed Beijing cutoff (DA C5 fix from the repo layer).
    """
    since_cutoff: str | None = None
    if since_days:
        # Compute cutoff in Beijing time rather than delegating to SQL now()
        import datetime as _dt

        from app.timeutil import BEIJING_TZ

        cutoff_dt = _dt.datetime.now(BEIJING_TZ).replace(tzinfo=None) - _dt.timedelta(
            days=since_days
        )
        since_cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

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


def get_notifications(
    conn: sqlite3.Connection,
    username: str,
    role: str,
) -> dict:
    """Return notification badge counts.

    Mirrors core.py:get_cicd_notifications.
    """
    return cicd_repo.notification_counts(
        conn, username, role, approver_roles=CICD_APPROVER_ROLES
    )


def list_deliveries(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
    role: str = "SPD",
    submitter: str | None = None,
) -> list[dict]:
    """Return dispatch_spd delivery requests.

    Mirrors core.py:list_cicd_deliveries.
    """
    items = cicd_repo.list_deliveries(
        conn,
        status_filter=status_filter,
        submitter=submitter,
    )
    _attach_task_info(conn, items)
    return [_strip_request(r) for r in items]


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def submit_request(
    conn: sqlite3.Connection,
    *,
    task_id: str | None,
    request_type: str,
    payload: dict,
    submitter: str,
    submitter_role: str,
    submitter_display: str = "",
) -> dict:
    """Submit a CICD task create/modify request.

    RM/Admin: auto-approves immediately and applies.
    Owner: enters pending queue.
    Returns the raw request row (payload as JSON string, mirroring core.py).

    Mirrors core.py:submit_cicd_request.
    """
    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM、Admin 可以提交 CICD 任务申请")
    _validate_payload_fields(payload)
    is_auto = submitter_role in CICD_APPROVER_ROLES
    ts = beijing_timestamp()
    with transaction(conn):
        req_id = cicd_repo.insert_request(
            conn,
            task_id=task_id,
            request_type=request_type,
            payload=payload,
            submitter=submitter,
            submitter_display=submitter_display,
            submitted_at=ts,
            status="approved" if is_auto else "pending",
            reviewer=submitter if is_auto else "",
            reviewed_at=ts if is_auto else "",
            review_note="",
            is_self_approved=1 if is_auto else 0,
        )
        if is_auto:
            _apply_cicd_request(conn, req_id, payload, task_id, request_type, ts)
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    # Return raw row dict (payload as JSON string) — mirrors core.py:submit_cicd_request
    return _strip_request(dict(row))


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
    """Approve a pending CICD request.

    approval_mode='immediate': apply change right away.
    approval_mode='dispatch_spd': defer apply until SPD delivers.
    Returns the raw request row dict.

    Mirrors core.py:approve_cicd_request.
    """
    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以审批 CICD 任务申请")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，无法审批")
    ts = beijing_timestamp()
    import json as _json

    payload = _json.loads(req["payload"] or "{}")
    with transaction(conn):
        if approval_mode == "dispatch_spd":
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='dispatch_spd', delivery_status='pending',
                       jira_id=?, jira_auto_created=?
                   WHERE id=?""",
                (reviewer, ts, review_note, jira_id, jira_auto_created, req_id),
            )
        else:
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='immediate'
                   WHERE id=?""",
                (reviewer, ts, review_note, req_id),
            )
            _apply_cicd_request(
                conn, req_id, payload, req["task_id"], req["request_type"], ts
            )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def reject_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    reviewer: str,
    reviewer_role: str,
    review_note: str,
) -> dict:
    """Reject a pending CICD request.

    Mirrors core.py:reject_cicd_request.
    """
    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以拒绝 CICD 任务申请")
    if not review_note or not review_note.strip():
        raise ValueError("拒绝必须填写理由")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    if dict(row)["status"] != "pending":
        raise RuntimeError(f"申请状态为 {dict(row)['status']}，无法拒绝")
    ts = beijing_timestamp()
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_task_requests "
            "SET status='rejected', reviewer=?, reviewed_at=?, review_note=? WHERE id=?",
            (reviewer, ts, review_note.strip(), req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def cancel_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    username: str,
    role: str,
) -> dict:
    """Cancel a pending CICD request.

    Mirrors core.py:cancel_cicd_request.
    """
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，只有 pending 状态可以取消")
    if req["submitter"] != username and role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有提交人或 RM/Admin 可以取消申请")
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_task_requests SET status='cancelled', reviewed_at=? WHERE id=?",
            (beijing_timestamp(), req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def transfer_owner(
    conn: sqlite3.Connection,
    task_id: str,
    new_owner: str,
    *,
    actor: str,
    actor_role: str,
) -> dict:
    """Transfer task ownership directly (RM/Admin only, no approval needed).

    Mirrors core.py:transfer_cicd_owner.
    """
    import json as _json

    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以直接修改负责人")
    task = cicd_repo.get_task(conn, task_id)
    if not task:
        raise RuntimeError(f"CICD 任务 {task_id} 不存在")
    old_owner = task["owner_username"]
    ts = beijing_timestamp()
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_tasks SET owner_username=?, updated_at=? WHERE id=?",
            (new_owner, ts, task_id),
        )
        payload = _json.dumps(
            {"owner_username": {"old": old_owner, "new": new_owner}},
            ensure_ascii=False,
        )
        conn.execute(
            """
            INSERT INTO cicd_task_requests
              (task_id, request_type, payload, submitter, submitter_display,
               submitted_at, status, reviewer, reviewed_at, review_note, is_self_approved)
            VALUES (?, 'owner_transfer', ?, ?, ?, ?, 'approved', ?, ?, '负责人直接变更', 1)
            """,
            (task_id, payload, actor, actor, ts, actor, ts),
        )
    updated = cicd_repo.get_task(conn, task_id)
    _attach_owner_display(conn, [updated])
    return _strip_task(updated)


def delete_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    actor_role: str,
) -> None:
    """Delete an Abandoned CICD task and all its history (RM/Admin only).

    Mirrors core.py:delete_cicd_task.
    """
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以删除 CICD 任务")
    task = cicd_repo.get_task(conn, task_id)
    if not task:
        raise RuntimeError(f"CICD 任务 {task_id} 不存在")
    if task["status"] != "Abandoned":
        raise RuntimeError("只有 Abandoned 状态的任务可以删除")
    with transaction(conn):
        cicd_repo.delete_task(conn, task_id)


def mark_visited(
    conn: sqlite3.Connection,
    username: str,
) -> None:
    """Update last_visited_at for notification badge.

    Mirrors core.py:mark_cicd_visited.
    """
    ts = beijing_timestamp()
    with transaction(conn):
        cicd_repo.mark_notification_visited(conn, username, ts)


def deliver_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    deliverer: str,
    deliverer_role: str,
) -> dict:
    """SPD (or RM/Admin) marks a dispatched request as delivered.

    Mirrors core.py:deliver_cicd_request.
    """
    import json as _json

    if deliverer_role not in {"SPD", "RM", "Admin"}:
        raise PermissionError("只有 SPD、RM、Admin 可以标记已交付")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req.get("delivery_status") not in ("pending", "returned"):
        raise RuntimeError(
            f"该申请的交付状态为 '{req.get('delivery_status')}'，无法标记已交付"
        )
    ts = beijing_timestamp()
    payload = _json.loads(req["payload"] or "{}")
    with transaction(conn):
        _apply_cicd_request(
            conn, req_id, payload, req["task_id"], req["request_type"], ts
        )
        conn.execute(
            """UPDATE cicd_task_requests
               SET delivery_status='delivered', delivered_by=?, delivered_at=?
               WHERE id=?""",
            (deliverer, ts, req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def return_delivery(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    returner: str,
    returner_role: str,
    reason: str,
) -> dict:
    """SPD returns a delivery back to RM with a reason.

    Mirrors core.py:return_cicd_request.
    """
    if returner_role != "SPD":
        raise PermissionError("只有 SPD 可以退回交付申请")
    if not reason or not reason.strip():
        raise ValueError("退回必须填写原因")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req.get("delivery_status") != "pending":
        raise RuntimeError(
            f"该申请的交付状态为 '{req.get('delivery_status')}'，无法退回"
        )
    ts = beijing_timestamp()
    with transaction(conn):
        conn.execute(
            """UPDATE cicd_task_requests
               SET delivery_status='returned', returned_reason=?, returned_at=?
               WHERE id=?""",
            (reason.strip(), ts, req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def re_dispatch_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    actor: str,
    actor_role: str,
) -> dict:
    """RM/Admin re-dispatches a returned delivery back to SPD.

    Mirrors core.py:re_dispatch_cicd_request.
    """
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以重新下发")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req.get("delivery_status") != "returned":
        raise RuntimeError(
            f"该申请的交付状态为 '{req.get('delivery_status')}'，只有 returned 状态可以重新下发"
        )
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_task_requests SET delivery_status='pending' WHERE id=?",
            (req_id,),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


def apply_returned_request(
    conn: sqlite3.Connection,
    req_id: int,
    *,
    actor: str,
    actor_role: str,
) -> dict:
    """RM/Admin applies a returned (or pending-delivery) request immediately.

    Mirrors core.py:apply_returned_cicd_request.
    """
    import json as _json

    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM/Admin 可以直接生效")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req.get("delivery_status") not in ("pending", "returned"):
        raise RuntimeError(
            f"该申请的交付状态为 '{req.get('delivery_status')}'，无法直接生效"
        )
    ts = beijing_timestamp()
    payload = _json.loads(req["payload"] or "{}")
    with transaction(conn):
        _apply_cicd_request(
            conn, req_id, payload, req["task_id"], req["request_type"], ts
        )
        conn.execute(
            """UPDATE cicd_task_requests
               SET delivery_status='delivered', delivered_by=?, delivered_at=?,
                   approval_mode='immediate'
               WHERE id=?""",
            (actor, ts, req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


# ---------------------------------------------------------------------------
# Phase 4 stubs — do NOT implement; raise NotImplementedError
# ---------------------------------------------------------------------------


def create_task_request(
    conn: sqlite3.Connection,
    *,
    submitter: str,
    submitter_role: str,
    payload: dict,
) -> dict:
    """Create a pending CICD task create/modify request (Ruling-B: always pending).

    # TODO Phase 4
    """
    raise NotImplementedError


def approve_request_ruling(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    reviewer: str,
    approval_mode: str,
) -> dict:
    """Approve a pending CICD request (RM only; may self-approve).

    # TODO Phase 4
    """
    raise NotImplementedError


def abandon_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    reviewer: str,
) -> dict:
    """RM direct action: transition a Stopped task to Abandoned (Ruling-A gate).

    # TODO Phase 4
    """
    raise NotImplementedError


def sync_decision_to_cicd(
    conn: sqlite3.Connection,
    app_id: str,
    release_decision: str,
    *,
    submitter: str,
    origin: str = "release_decision_sync",
) -> dict | None:
    """Create a pending modify request to update the task's status.

    Called inside the same transaction as update_snapshot (plan §3.5 b).

    # TODO Phase 4
    """
    raise NotImplementedError


def cicd_first_new_app(
    conn: sqlite3.Connection,
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter: str,
    submitter_role: str,
    payload: dict,
) -> dict:
    """CICD-first app creation (plan §3.5 a, POST /api/cicd/apps/new).

    # TODO Phase 4
    """
    raise NotImplementedError
