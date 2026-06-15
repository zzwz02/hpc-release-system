"""CICD service — task and request management.

Faithful port of release_system/core.py:3639-4364 plus Phase 4 rulings.

Wave 1 (implemented): Ruling B (no auto-approve), Ruling C (Admin out of CICD).
Wave 2 (implemented): Ruling D (sync_decision_to_cicd), Ruling A (abandon_task),
    V3 status-lock (submit modify rejects status field).
Wave 3 (implemented): cicd_first_new_app, preview_cicd_app_info (fetch-preview wizard),
    app_info attachment (owner_confirmed=True), 1:1 cardinality dedup gate.
"""
from __future__ import annotations

import sqlite3

from app.db.connection import transaction
from app.repositories import cicd_repo
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Role constants — Ruling C (Admin out of CICD/release business)
# CICD_APPROVER_ROLES={RM}: Admin no longer approves CICD requests (plan §3.7, DA V2)
# CICD_CREATE_ROLES={Owner,RM}: Admin no longer submits CICD requests
# ---------------------------------------------------------------------------
CICD_APPROVER_ROLES: frozenset[str] = frozenset({"RM"})
CICD_CREATE_ROLES: frozenset[str] = frozenset({"Owner", "RM"})
CICD_STATUSES: frozenset[str] = frozenset({"Running", "Stopped", "Abandoned"})

# Ruling D: release_decision → CICD task status (plan §3.5 b)
# release/cicd_only → Running; stopped → Stopped (uppercase CICD_STATUSES vocab)
_DECISION_TO_CICD_STATUS: dict[str, str] = {
    "release": "Running",
    "cicd_only": "Running",
    "stopped": "Stopped",
}


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
    # Work on a local copy so we can extract internal-only fields without
    # mutating the caller's dict and without exposing them to field validation.
    payload = dict(payload)
    # app_id is an internal-only field injected by cicd_first_new_app to link
    # the new task to its parent app on approval.  Regular user create requests
    # omit it (None) — submit_request validation already blocks unknown fields.
    linked_app_id: str | None = payload.pop("app_id", None) or None
    _validate_payload_fields(payload)
    if request_type == "create":
        new_id = cicd_repo.next_cicd_id(conn)
        cicd_repo.create_task(
            conn,
            task_id=new_id,
            app_id=linked_app_id,  # None for regular creates; set for CICD-first
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

    Ruling B: ALL submissions → status="pending"; no auto-approve path.
    Approval happens ONLY via approve_request, performed by an RM.
    Returns the raw request row (payload as JSON string, mirroring core.py).

    Mirrors core.py:submit_cicd_request.  Auto-approve path removed (DA V1).
    """
    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以提交 CICD 任务申请")
    _validate_payload_fields(payload)
    # V3 / status-lock: user-submitted MODIFY requests must NOT touch status.
    # Only decision-sync (sync_decision_to_cicd) and abandon_task write status.
    if request_type == "modify" and "status" in (payload or {}):
        raise RuntimeError(
            "CICD 修改申请不允许直接修改运行状态；运行/停止由 App 决策驱动（Ruling A/D）"
        )
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
            status="pending",
            reviewer="",
            reviewed_at="",
            review_note="",
            is_self_approved=0,
        )
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
        raise PermissionError("只有 RM 可以审批 CICD 任务申请")
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，无法审批")
    # Ruling B: RM approving their own request sets is_self_approved=1 (audit flag)
    is_self_approved = 1 if reviewer == req["submitter"] else 0
    ts = beijing_timestamp()
    import json as _json

    payload = _json.loads(req["payload"] or "{}")
    with transaction(conn):
        if approval_mode == "dispatch_spd":
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='dispatch_spd', delivery_status='pending',
                       jira_id=?, jira_auto_created=?, is_self_approved=?
                   WHERE id=?""",
                (reviewer, ts, review_note, jira_id, jira_auto_created, is_self_approved, req_id),
            )
        else:
            conn.execute(
                """UPDATE cicd_task_requests
                   SET status='approved', reviewer=?, reviewed_at=?, review_note=?,
                       approval_mode='immediate', is_self_approved=?
                   WHERE id=?""",
                (reviewer, ts, review_note, is_self_approved, req_id),
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
        raise PermissionError("只有 RM 可以拒绝 CICD 任务申请")
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
        raise PermissionError("只有提交人或 RM 可以取消申请")
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
    """Transfer task ownership directly (RM only, no approval needed; Ruling C).

    Mirrors core.py:transfer_cicd_owner.
    """
    import json as _json

    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以直接修改负责人")
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
    """Delete an Abandoned CICD task and all its history (RM only; Ruling C).

    Mirrors core.py:delete_cicd_task.
    """
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以删除 CICD 任务")
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
    """SPD (or RM) marks a dispatched request as delivered (Ruling C: Admin excluded).

    Mirrors core.py:deliver_cicd_request.
    """
    import json as _json

    if deliverer_role not in {"SPD", "RM"}:
        raise PermissionError("只有 SPD、RM 可以标记已交付")
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
    """RM re-dispatches a returned delivery back to SPD (Ruling C: Admin excluded).

    Mirrors core.py:re_dispatch_cicd_request.
    """
    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以重新下发")
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
    """RM applies a returned (or pending-delivery) request immediately (Ruling C: Admin excluded).

    Mirrors core.py:apply_returned_cicd_request.
    """
    import json as _json

    if actor_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以直接生效")
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
# Ruling D — decision→CICD status sync (plan §3.5 b)
# ---------------------------------------------------------------------------


def sync_decision_to_cicd(
    conn: sqlite3.Connection,
    app_id: str,
    release_decision: str,
    *,
    submitter: str,
    origin: str = "release_decision_sync",
) -> dict | None:
    """Create a pending modify request to sync the CICD task's running/stopped state.

    Called INSIDE the same transaction as update_snapshot (plan §3.5 b).
    Returns the created pending request dict, or None when no-op.

    No-op cases:
    * App has no linked CICD task (app_id not linked yet).
    * Task is already at the target status.
    * A pending modify-on-status request already exists (idempotent guard, plan P4).
    """
    target_status = _DECISION_TO_CICD_STATUS.get(release_decision)
    if not target_status:
        return None  # unknown decision value — defensive no-op

    tasks = cicd_repo.tasks_for_app(conn, app_id)
    if not tasks:
        return None  # app not yet linked to any CICD task

    task = tasks[0]  # 1:1 cardinality: at most one linked task per app
    current_status = task.get("status", "")

    if current_status == target_status:
        return None  # already at target — no-op (idempotent)

    # Idempotent guard: skip if there is already a pending modify touching status
    if cicd_repo.has_open_modify_on_field(conn, task["id"], "status"):
        return None

    ts = beijing_timestamp()
    payload = {"status": {"old": current_status, "new": target_status}}
    req_id = cicd_repo.insert_request(
        conn,
        task_id=task["id"],
        request_type="modify",
        payload=payload,
        submitter=submitter,
        submitter_display="",
        submitted_at=ts,
        status="pending",
        reviewer="",
        reviewed_at="",
        review_note="",
        is_self_approved=0,
        origin=origin,
    )
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    return _strip_request(dict(row))


# ---------------------------------------------------------------------------
# Ruling A — abandon task (plan §3.5 c)
# ---------------------------------------------------------------------------


def abandon_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    reviewer: str,
    reviewer_role: str,
) -> dict:
    """RM direct action: transition a Stopped task to Abandoned (terminal).

    Like transfer_owner, this is a direct governance action — no pending queue.
    Only Stopped tasks can be abandoned (Ruling A: stopping happens only via
    App decision, so by the time a task reaches Abandoned it must have gone
    through Stopped first).
    Returns the updated task dict.

    Mirrors plan §3.5 c.
    """
    import json as _json

    if reviewer_role not in CICD_APPROVER_ROLES:
        raise PermissionError("只有 RM 可以废弃 CICD 任务")
    task = cicd_repo.get_task(conn, task_id)
    if not task:
        raise RuntimeError(f"CICD 任务 {task_id} 不存在")
    if task["status"] != "Stopped":
        raise RuntimeError(
            "只有 Stopped 状态的任务可以废弃（Ruling A：停止只能经 App 决策驱动）"
        )
    ts = beijing_timestamp()
    payload_json = _json.dumps(
        {"status": {"old": "Stopped", "new": "Abandoned"}},
        ensure_ascii=False,
    )
    with transaction(conn):
        conn.execute(
            "UPDATE cicd_tasks SET status='Abandoned', updated_at=? WHERE id=?",
            (ts, task_id),
        )
        # Write an approved audit record (mirrors transfer_owner pattern)
        conn.execute(
            """
            INSERT INTO cicd_task_requests
              (task_id, request_type, payload, submitter, submitter_display,
               submitted_at, status, reviewer, reviewed_at, review_note,
               is_self_approved, origin)
            VALUES (?, 'modify', ?, ?, ?, ?, 'approved', ?, ?, '废弃/退役任务', 0, 'abandon')
            """,
            (task_id, payload_json, reviewer, reviewer, ts, reviewer, ts),
        )
    updated = cicd_repo.get_task(conn, task_id)
    _attach_owner_display(conn, [updated])
    return _strip_task(updated)


# ---------------------------------------------------------------------------
# Wave 3 — CICD-first app creation (plan §3.5 a)
# ---------------------------------------------------------------------------


def _find_app_by_identity(
    conn: sqlite3.Connection,
    git_url: str,
    git_branch: str,
) -> dict | None:
    """Return the app dict whose (git_url, git_branch) matches the derived identity.

    Both sides are normalised through same_identity() / normalize_git_url() so
    that short repo names in the DB ('hpc_hpl') compare equal to the full
    SSH URL produced by repo_to_git_identity (plan §4.2 规范化对齐).
    Returns None when no match is found.
    """
    from app.identity import same_identity

    rows = conn.execute("SELECT id, git_url, git_branch FROM apps").fetchall()
    for row in rows:
        if same_identity(row["git_url"], row["git_branch"], git_url, git_branch):
            return dict(row)
    return None


def _has_pending_cicd_create_for_app(
    conn: sqlite3.Connection,
    app_id: str,
) -> bool:
    """True if a pending 'create' CICD request already carries this app_id.

    Guards against duplicate pending create requests from repeated CICD-first
    calls before RM approves the first one.
    """
    import json as _json

    rows = conn.execute(
        "SELECT payload FROM cicd_task_requests "
        "WHERE status = 'pending' AND request_type = 'create'"
    ).fetchall()
    for row in rows:
        try:
            p = _json.loads(row["payload"] or "{}")
        except Exception:
            continue
        if p.get("app_id") == app_id:
            return True
    return False


def preview_cicd_app_info(
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter_role: str,
    _fetch_fn=None,
) -> dict:
    """Fetch and parse Gerrit app_info for a CICD-first preview (NO DB writes).

    Returns a preview dict with key identity + app_info fields so the
    frontend can show the user what will be created before they confirm.
    Also returns the full `parsed` blob for passing back to
    cicd_first_new_app as `app_info_parsed`.

    Args:
        repo_type: advisory repo type (git/repo/manifest)
        repo_name: short repo name or .xml manifest path
        branch: git branch / revision
        submitter_role: caller's role (auth check already done in router)
        _fetch_fn: injectable fetch function for tests; defaults to
                   app.integrations.gerrit.fetch_app_info.  Tests can
                   pass a mock directly without patching at the module level.

    Raises:
        ValueError: identity resolution failed (bad repo_name/branch)
        GerritNetworkError: Gerrit unreachable or archive fetch failed → HTTP 502
    """
    import release_system.core as core

    from app.api.errors import GerritNetworkError
    from app.config import settings
    from app.identity import repo_to_git_identity

    # Derive identity (offline for short names; network only for .xml manifests)
    git_url, git_branch = repo_to_git_identity(repo_type, repo_name, branch)
    if not git_url or not git_branch:
        raise ValueError(
            "无法解析 repo 身份（repo_name 为空或 .xml manifest 解析失败），"
            "请检查 repo_name / branch"
        )

    if _fetch_fn is None:
        from app.integrations.gerrit import fetch_app_info as _default_fetch

        _fetch_fn = _default_fetch

    try:
        raw_json, commit_id = _fetch_fn(
            git_url,
            git_branch,
            project_root=settings.db_path.parent,
        )
    except Exception as exc:
        raise GerritNetworkError(
            f"无法从 Gerrit 获取 app_info.json（{git_url}@{git_branch}）：{exc}"
        ) from exc

    parsed = core.parse_app_info(raw_json)

    return {
        "git_url": git_url,
        "git_branch": git_branch,
        "app_version": parsed.get("app_version", ""),
        "x86_chips": ",".join(core.order_chips(parsed.get("x86_chips", []))),
        "arm_chips": ",".join(core.order_chips(parsed.get("arm_chips", []))),
        "python_label": ",".join(parsed.get("python_labels", [])),
        "pytorch_label": ",".join(parsed.get("pytorch_labels", [])),
        "os": ",".join(parsed.get("build_os", [])),
        "arch": ",".join(parsed.get("build_arches", [])),
        "commit_id": commit_id,
        "parsed": parsed,  # full blob — pass to cicd_first_new_app as app_info_parsed
    }


def _apply_parsed_app_info_to_snapshot(
    snapshot: dict,
    parsed: dict,
    *,
    submitter: str,
    commit_id: str = "",
) -> None:
    """Apply a pre-parsed app_info blob to a snapshot dict in-place.

    No DB access, no audit, no phase checks — used only when attaching the
    owner-confirmed app_info at CICD-first create time (the app is brand new,
    no existing content to diff against).

    Sets owner_confirmed=True because the submitter explicitly provided the
    app_info (equivalent to confirming the content they fetched).
    """
    import release_system.core as core

    snapshot["app_info"] = {
        "source": f"{parsed.get('app_name', '')} (cicd_first)",
        "source_type": "cicd_workbench",
        "synced_at": beijing_timestamp(),
        "commit_id": commit_id,
        "uploaded_by": submitter,
        "raw": parsed.get("raw", {}),
        "parsed": parsed,
    }
    snapshot["app_info_diffs"] = []  # no previous version to diff against
    snapshot["version"] = parsed.get("app_version", "") or snapshot.get("version", "")
    snapshot["x86_chips"] = ",".join(core.order_chips(parsed.get("x86_chips", [])))
    snapshot["arm_chips"] = ",".join(core.order_chips(parsed.get("arm_chips", [])))
    snapshot["python_labels"] = ",".join(parsed.get("python_labels", []))
    snapshot["pytorch_labels"] = ",".join(parsed.get("pytorch_labels", []))
    snapshot["build_os"] = ",".join(parsed.get("build_os", []))
    snapshot["build_arches"] = ",".join(parsed.get("build_arches", []))
    snapshot["owner_confirmed"] = True  # submitter confirmed by providing app_info


def cicd_first_new_app(
    conn: sqlite3.Connection,
    *,
    official_name: str,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter: str,
    submitter_role: str,
    submitter_display: str = "",
    payload: dict,
    app_info_parsed: dict | None = None,
    app_info_commit_id: str = "",
) -> dict:
    """CICD-first app creation (plan §3.5 a, POST /api/cicd/apps/new).

    Request body carries repo info but NO git_url/git_branch — those are
    derived via the app/identity.py seam (may do network I/O for .xml
    manifests) which MUST run OUTSIDE the write transaction.

    One outer transaction wraps:
      - app row + initial snapshot(cicd_only) in all unlocked releases
        (OR: locate existing CICD-less orphan app, skip creation)
      - optional app_info attachment to all unlocked snapshots (when
        app_info_parsed is provided — sets owner_confirmed=True)
      - pending CICD 'create' request (Ruling B: always pending)

    The actual cicd_task row lands only when RM approves (ruling B).
    app_id is embedded in the request payload so _apply_cicd_request links
    the new task to its parent app on approval.

    1:1 cardinality ruling:
      - derived identity matches existing app that has a CICD task
        → reject "该 app 已有 CICD 任务"
      - derived identity matches existing app with NO CICD task (orphan)
        → associate: create pending create request for that app
      - no existing app → create new app + initial cicd_only snapshots

    Optional app_info:
      - app_info_parsed: full parsed dict from preview_cicd_app_info()
      - app_info_commit_id: git commit id for source attribution
      When provided, applied inline to all unlocked snapshots for the app
      and owner_confirmed is set to True (owner confirmed the Gerrit content).
    """
    import json as _json

    import release_system.core as core

    from app.identity import repo_to_git_identity

    # ------------------------------------------------------------------
    # Role check (mirrors CICD_CREATE_ROLES; Admin excluded — Ruling C)
    # ------------------------------------------------------------------
    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以发起 CICD-first 建 app")

    official_name = (official_name or "").strip()
    if not official_name:
        raise ValueError("必须提供 app 名称（official_name）")

    # ------------------------------------------------------------------
    # Step 1 — Derive identity OUTSIDE the write transaction
    # (manifest fetches involve network I/O via git archive --remote)
    # ------------------------------------------------------------------
    git_url, git_branch = repo_to_git_identity(repo_type, repo_name, branch)
    if not git_url or not git_branch:
        raise ValueError(
            "无法解析 repo 身份（repo_name 为空或 .xml manifest 解析失败），"
            "请检查 repo_name / branch"
        )

    # ------------------------------------------------------------------
    # Step 2 — Single write transaction: dedup gate + app + request
    # ------------------------------------------------------------------
    with transaction(conn):
        # ---- dedup gate: find existing app with normalised identity ----
        existing_app = _find_app_by_identity(conn, git_url, git_branch)

        if existing_app:
            app_id = existing_app["id"]
            # 1:1 check: existing tasks?
            existing_tasks = cicd_repo.tasks_for_app(conn, app_id)
            if existing_tasks:
                raise RuntimeError(
                    f"该 app（{app_id}）已有 CICD 任务（{existing_tasks[0]['id']}），"
                    "无法重复创建"
                )
            # Idempotency guard: pending create already waiting for approval?
            if _has_pending_cicd_create_for_app(conn, app_id):
                raise RuntimeError(
                    f"该 app（{app_id}）已有待审批的 CICD 创建申请，请等待 RM 审批"
                )
            action = "associated"

        else:
            # ---- No existing app: create new app + initial cicd_only snapshots ----
            # Find anchor release (first unlocked) so add_new_app_request can
            # propagate the snapshot to it and all subsequent unlocked releases.
            releases = core.list_releases(conn)
            unlocked = [r for r in releases if not r.get("released_locked")]
            if not unlocked:
                raise RuntimeError("没有可用的未锁定 release，无法创建 app")
            anchor_release_id = unlocked[0]["id"]

            # Reuse core's single dedup gate (git_url/git_branch unique check +
            # id allocation + current/future release forward-sync).
            # core.add_new_app_request internally opens its own transaction()
            # context; because conn is app.db.connection.ManagedConnection and
            # _transaction_depth is already > 0, core's commit() calls are
            # suppressed — all writes batch into our outer transaction.
            app_id = core.add_new_app_request(
                conn,
                anchor_release_id,
                official_name=official_name,
                git_url=git_url,
                git_branch=git_branch,
                release_decision="cicd_only",
                owner=submitter,
                doc_target="manual",
            )
            action = "created"

        # ---- Optional: attach app_info to all unlocked snapshots ----
        # When the caller ran fetch-preview first and passes the parsed blob,
        # we apply it inline (no round-trip, no phase checks needed — the app
        # is brand new so there is no previous content to conflict with).
        # owner_confirmed is set to True because the submitter explicitly
        # confirmed the Gerrit content they fetched.
        if app_info_parsed:
            releases = core.list_releases(conn)
            for rel in releases:
                if rel.get("released_locked"):
                    continue
                rel_full = core.get_release(conn, rel["id"])
                snap = rel_full["snapshots"].get(app_id)
                if snap is None:
                    continue
                _apply_parsed_app_info_to_snapshot(
                    snap,
                    app_info_parsed,
                    submitter=submitter,
                    commit_id=app_info_commit_id,
                )
                core.save_snapshot(conn, rel["id"], app_id, snap)

        # ---- Create pending CICD 'create' request (Ruling B: always pending) ----
        # Embed app_id in payload: when RM approves, _apply_cicd_request will
        # pop it and pass it to cicd_repo.create_task so the new task is linked.
        create_payload: dict = {
            "app_name": payload.get("app_name") or official_name,
            "app_id": app_id,                            # internal linkage field
            "repo_type": repo_type,
            "repo_name": repo_name,
            "branch": branch,
            "app_version": payload.get("app_version", ""),
            "build_product": payload.get("build_product", []),
            "community_artifact": payload.get("community_artifact", []),
            "build_image": payload.get("build_image", ""),
            "test_timeout": int(payload.get("test_timeout") or 40),
            "owner_username": submitter,
            "status": "Running",  # initial task status — aligned with cicd_only decision
            "notes": payload.get("notes", ""),
        }

        ts = beijing_timestamp()
        req_id = cicd_repo.insert_request(
            conn,
            task_id=None,
            request_type="create",
            payload=create_payload,
            submitter=submitter,
            submitter_display=submitter_display,
            submitted_at=ts,
            status="pending",
            reviewer="",
            reviewed_at="",
            review_note="",
            is_self_approved=0,
            origin="cicd_workbench",
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
        req = _strip_request(dict(row))

    return {
        "ok": True,
        "action": action,        # "created" | "associated"
        "app_id": app_id,
        "git_url": git_url,
        "git_branch": git_branch,
        "request": req,
    }
