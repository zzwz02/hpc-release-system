"""CICD service — task and request management.

Faithful port of release_system/core.py:3639-4364 plus Phase 4 rulings.

Wave 1 (implemented): Ruling B (no auto-approve), Ruling C (Admin out of CICD).
Wave 2 (implemented): Ruling D (sync_decision_to_cicd),
    V3 status-lock (submit modify rejects status field).
Wave 3 (implemented): cicd_first_new_app, preview_cicd_app_info (fetch-preview wizard),
    app_info attachment (owner_confirmed=True), 1:1 cardinality dedup gate.
"""
from __future__ import annotations

import sqlite3

from app.db.connection import transaction
from app.repositories import apps_repo, cicd_repo, releases_repo, snapshots_repo
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# Role constants — Ruling C (Admin out of CICD/release business)
# CICD_APPROVER_ROLES={RM}: Admin no longer approves CICD requests (plan §3.7, DA V2)
# CICD_CREATE_ROLES={Owner,RM}: Admin no longer submits CICD requests
# ---------------------------------------------------------------------------
CICD_APPROVER_ROLES: frozenset[str] = frozenset({"RM"})
CICD_CREATE_ROLES: frozenset[str] = frozenset({"Owner", "RM"})
CICD_STATUSES: frozenset[str] = frozenset({"Running", "Stopped"})

# Ruling D: release_decision → CICD task status (plan §3.5 b)
# release/cicd_only → Running; stopped → Stopped (uppercase CICD_STATUSES vocab)
_DECISION_TO_CICD_STATUS: dict[str, str] = {
    "release": "Running",
    "cicd_only": "Running",
    "stopped": "Stopped",
}

_APP_CICD_FIELD_TO_PAYLOAD_FIELD: dict[str, str] = {
    "cicd_repo_type": "repo_type",
    "cicd_community_artifact": "community_artifact",
    "cicd_build_image": "build_image",
    "cicd_test_timeout": "test_timeout",
    "cicd_notes": "notes",
}

_PAYLOAD_FIELD_TO_APP_CICD_FIELD: dict[str, str] = {
    value: key for key, value in _APP_CICD_FIELD_TO_PAYLOAD_FIELD.items()
}

_APP_CICD_MUTABLE_FIELDS: frozenset[str] = frozenset(_PAYLOAD_FIELD_TO_APP_CICD_FIELD)
_APP_BACKED_MUTABLE_FIELDS: frozenset[str] = _APP_CICD_MUTABLE_FIELDS | frozenset(
    {"repo_name", "branch"}
)

_CICD_FIRST_ACTION_FIELD = "_cicd_first_action"
_CICD_FIRST_TARGET_DECISION_FIELD = "_cicd_first_target_decision"
_DECISION_SYNC_RELEASE_ID_FIELD = "_decision_sync_release_id"
_DECISION_SYNC_TARGET_DECISION_FIELD = "_decision_sync_target_decision"
_DECISION_SYNC_ROLLBACK_FIELD = "_decision_sync_rollback"
_CICD_FIRST_ACTION_CREATED = "created"
_CICD_FIRST_ACTION_ASSOCIATED = "associated"
_INTERNAL_PAYLOAD_FIELDS: frozenset[str] = frozenset(
    {
        _CICD_FIRST_ACTION_FIELD,
        _CICD_FIRST_TARGET_DECISION_FIELD,
        _DECISION_SYNC_RELEASE_ID_FIELD,
        _DECISION_SYNC_TARGET_DECISION_FIELD,
        _DECISION_SYNC_ROLLBACK_FIELD,
    }
)


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


def _latest_release_id(conn: sqlite3.Connection) -> str | None:
    releases = releases_repo.list_release_rows(conn)
    return releases[-1]["id"] if releases else None


def _snapshot_for_app_latest(conn: sqlite3.Connection, app_id: str) -> dict:
    release_id = _latest_release_id(conn)
    if not release_id:
        return {}
    return snapshots_repo.get_snapshot(conn, release_id, app_id) or {}


def _app_display_name(conn: sqlite3.Connection, app_id: str, fallback: str = "") -> str:
    snapshot = _snapshot_for_app_latest(conn, app_id)
    if snapshot.get("official_name"):
        return str(snapshot["official_name"])
    app = apps_repo.get_app(conn, app_id) or {}
    aliases = app.get("aliases") or []
    if aliases:
        return str(aliases[0])
    return fallback or app_id


def _status_from_snapshot(snapshot: dict) -> str:
    return _DECISION_TO_CICD_STATUS.get(
        (snapshot.get("release_decision") or "release").strip(),
        "Running",
    )


def _community_payload_from_app(value: str | None) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    aliases = {"image": "image", "镜像": "image", "pkg": "pkg", "package": "pkg", "软件包": "pkg"}
    items: list[str] = []
    for part in raw.replace("，", ",").split(","):
        mapped = aliases.get(part.strip())
        if mapped and mapped not in items:
            items.append(mapped)
    return items


def _community_app_value(value: object) -> str:
    aliases = {"image": "image", "镜像": "image", "pkg": "pkg", "package": "pkg", "软件包": "pkg"}
    if isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = str(value or "").replace("，", ",").split(",")
    items: list[str] = []
    for item in raw_items:
        mapped = aliases.get(item.strip())
        if mapped and mapped not in items:
            items.append(mapped)
    return ", ".join(items)


def _test_timeout_value(value: object) -> int:
    try:
        parsed = int(str(value or "").strip() or "40")
    except ValueError:
        parsed = 40
    return parsed if parsed > 0 else 40


def _task_id_to_app_id(conn: sqlite3.Connection, task_id: str | None) -> str | None:
    if not task_id:
        return None
    if apps_repo.get_app(conn, task_id):
        return task_id
    return None


def _update_app_git_identity(
    conn: sqlite3.Connection,
    app_id: str,
    *,
    git_url: str | None = None,
    git_branch: str | None = None,
) -> None:
    app = apps_repo.get_app(conn, app_id)
    if not app:
        return
    next_url = str(git_url if git_url is not None else app.get("git_url", "")).strip()
    next_branch = str(git_branch if git_branch is not None else app.get("git_branch", "")).strip()
    if next_url == app.get("git_url", "") and next_branch == app.get("git_branch", ""):
        return
    collision = conn.execute(
        "SELECT id FROM apps WHERE git_url = ? AND git_branch = ? AND id != ?",
        (next_url, next_branch, app_id),
    ).fetchone()
    if collision:
        raise RuntimeError(
            f"该 Gerrit URL + branch 已被 app {collision['id']} 占用，不能改成相同值"
        )
    app["git_url"] = next_url
    app["git_branch"] = next_branch
    apps_repo.save_app(conn, app)


def _app_to_cicd_task(
    app: dict,
    snapshot: dict,
) -> dict:
    owners = snapshot.get("owners") or []
    owner_username = owners[0] if owners else ""
    aliases = app.get("aliases") or []
    fallback_name = aliases[0] if aliases else app["id"]
    return {
        "id": app["id"],
        "app_id": app["id"],
        "app_name": snapshot.get("official_name") or fallback_name,
        "app_version": snapshot.get("version", ""),
        "repo_type": app.get("cicd_repo_type") or "git",
        "repo_name": app.get("git_url", ""),
        "branch": app.get("git_branch", ""),
        "build_product": [],
        "community_artifact": _community_payload_from_app(app.get("cicd_community_artifact")),
        "build_image": app.get("cicd_build_image", ""),
        "test_timeout": _test_timeout_value(app.get("cicd_test_timeout")),
        "owner_username": owner_username,
        "status": _status_from_snapshot(snapshot),
        "notes": app.get("cicd_notes", ""),
        "created_at": app.get("created_at", ""),
        "updated_at": app.get("created_at", ""),
        "has_pending": False,
        "has_pending_delivery": False,
        "owner_display": owner_username,
    }


def _app_cicd_tasks(conn: sqlite3.Connection) -> list[dict]:
    tasks = [
        _app_to_cicd_task(
            app,
            _snapshot_for_app_latest(conn, app["id"]),
        )
        for app in apps_repo.list_apps(conn)
    ]
    task_to_app = {task["id"]: task["app_id"] for task in tasks}
    pending_task_ids = cicd_repo.pending_task_ids(conn)
    delivery_task_ids = cicd_repo.delivery_pending_task_ids(conn)
    pending_app_ids = {
        task_to_app.get(task_id, task_id)
        for task_id in pending_task_ids
    }
    delivery_app_ids = {
        task_to_app.get(task_id, task_id)
        for task_id in delivery_task_ids
    }
    for task in tasks:
        task["has_pending"] = task["app_id"] in pending_app_ids or task["id"] in pending_task_ids
        task["has_pending_delivery"] = task["app_id"] in delivery_app_ids or task["id"] in delivery_task_ids
    _apply_onboarding_state_to_tasks(conn, tasks)
    _apply_open_decision_status_overrides(conn, tasks)
    _attach_owner_display(conn, tasks)
    return tasks


def _apply_open_decision_status_overrides(
    conn: sqlite3.Connection,
    tasks: list[dict],
) -> None:
    """Keep displayed CICD status at the old value while decision-sync is open."""
    task_to_row = {task["id"]: task for task in tasks}
    app_to_row = {task["app_id"]: task for task in tasks if task.get("app_id")}
    rows = conn.execute(
        """
        SELECT COALESCE(app_id, task_id) AS app_ref, payload
        FROM cicd_task_requests
        WHERE origin = 'release_decision_sync'
          AND request_type = 'modify'
          AND (
            status = 'pending'
            OR delivery_status IN ('pending', 'returned')
          )
        ORDER BY submitted_at DESC, id DESC
        """
    ).fetchall()
    for row in rows:
        payload = cicd_repo._load_payload(row["payload"])
        change = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(change, dict):
            continue
        old_status = str(change.get("old") or "").strip()
        if not old_status:
            continue
        task_id = row["app_ref"] or ""
        task = task_to_row.get(task_id) or app_to_row.get(task_id)
        if task:
            task["status"] = old_status


def _attach_task_info(
    conn: sqlite3.Connection,
    items: list[dict],
) -> None:
    """Attach App-backed task display fields to a list of request dicts."""
    task_map = {task["id"]: task for task in _app_cicd_tasks(conn)}
    app_map = {task["app_id"]: task for task in task_map.values()}
    for d in items:
        app_ref = d.get("app_id") or d.get("task_id") or ""
        if d.get("app_id") and not d.get("task_id"):
            d["task_id"] = d["app_id"]
        t = task_map.get(app_ref, {}) or app_map.get(app_ref, {})
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


def _apply_app_identity(conn: sqlite3.Connection, tasks: list[dict]) -> None:
    """Use apps.git_url/git_branch as canonical identity for task-shaped rows."""
    app_ids = sorted({t.get("app_id") for t in tasks if t.get("app_id")})
    if not app_ids:
        return
    rows = conn.execute(
        "SELECT id, git_url, git_branch FROM apps WHERE id IN ({})".format(
            ",".join("?" * len(app_ids))
        ),
        app_ids,
    ).fetchall()
    app_map = {r["id"]: r for r in rows}
    for task in tasks:
        app_id = task.get("app_id")
        app = app_map.get(app_id)
        if not app:
            continue
        task["repo_name"] = app["git_url"]
        task["branch"] = app["git_branch"]


# ---------------------------------------------------------------------------
# Response-shape helpers — strip columns the old server never exposed
# ---------------------------------------------------------------------------

# app_id is exposed so the App Workbench can link CICD tasks by the relational
# association instead of guessing from mutable repo identity fields.
_TASK_STRIP: frozenset[str] = frozenset()

# origin was added in Phase 0 as an internal audit column; core.py never
# selected it so it never appeared in any golden request responses.
# F3 (follow-up): origin is now intentionally EXPOSED so the API returns
# 'cicd_workbench' | 'release_decision_sync' on every request object.
_REQUEST_STRIP: frozenset[str] = frozenset()


def _strip_task(t: dict) -> dict:
    """Remove Phase-0-only task columns that the old server never returned."""
    for k in _TASK_STRIP:
        t.pop(k, None)
    return t


def _strip_internal_payload_fields(payload: object) -> object:
    """Hide service-internal payload metadata from API callers."""
    if isinstance(payload, str):
        import json as _json

        parsed = cicd_repo._load_payload(payload)
        stripped = _strip_internal_payload_fields(parsed)
        return _json.dumps(stripped, ensure_ascii=False)
    if not isinstance(payload, dict):
        return payload
    return {
        key: value
        for key, value in payload.items()
        if key not in _INTERNAL_PAYLOAD_FIELDS
    }


def _strip_request(r: dict) -> dict:
    """Remove Phase-0-only request columns that the old server never returned."""
    if r.get("app_id") and not r.get("task_id"):
        r["task_id"] = r["app_id"]
    r["payload"] = _strip_internal_payload_fields(r.get("payload"))
    for k in _REQUEST_STRIP:
        r.pop(k, None)
    return r


def _apply_deferred_release_decision(
    conn: sqlite3.Connection,
    app_id: str,
    release_id: str,
    target_decision: str,
) -> None:
    """Apply a release decision that was waiting for CICD delivery."""
    import release_system.core as core

    decision = core.normalize_release_decision(target_decision)
    if decision not in core.RELEASE_DECISIONS:
        return
    snap = snapshots_repo.get_snapshot(conn, release_id, app_id)
    if not snap or snap.get("release_decision") == decision:
        return
    snap["release_decision"] = decision
    snapshots_repo.save_snapshot(conn, release_id, app_id, snap)


def _status_change(payload: dict) -> tuple[str, str]:
    change = payload.get("status") if isinstance(payload, dict) else None
    if not isinstance(change, dict):
        return "", ""
    return (
        str(change.get("old") or "").strip(),
        str(change.get("new") or "").strip(),
    )


def _is_release_decision_sync_stop_request(req: dict, payload: dict) -> bool:
    """True for release/cicd_only -> stopped status requests.

    These requests represent an owner/RM release-decision downgrade that has
    already taken effect in App snapshots. CICD approval is only the completion
    point for operational stopping, so RM must not reject it.
    """
    if req.get("origin") != "release_decision_sync" or req.get("request_type") != "modify":
        return False
    old_status, new_status = _status_change(payload)
    return old_status == "Running" and new_status == "Stopped"


def _is_release_decision_sync_start_request(req: dict, payload: dict) -> bool:
    if req.get("origin") != "release_decision_sync" or req.get("request_type") != "modify":
        return False
    old_status, new_status = _status_change(payload)
    return old_status == "Stopped" and new_status == "Running"


def _rollback_deferred_decision_sync(
    conn: sqlite3.Connection,
    app_id: str,
    payload: dict,
) -> None:
    """Undo release-decision fan-out for a rejected/cancelled start request."""
    import release_system.core as core

    entries = payload.get(_DECISION_SYNC_ROLLBACK_FIELD)
    if not isinstance(entries, list):
        return
    for item in entries:
        if not isinstance(item, dict):
            continue
        release_id = str(item.get("release_id") or "").strip()
        previous = str(item.get("previous_decision") or "").strip()
        applied = str(item.get("applied_decision") or "").strip()
        if not release_id or not previous:
            continue
        previous = core.normalize_release_decision(previous)
        applied = core.normalize_release_decision(applied) if applied else ""
        snap = snapshots_repo.get_snapshot(conn, release_id, app_id)
        if not snap:
            continue
        current = core.normalize_release_decision(snap.get("release_decision", "release"))
        if applied and current != applied:
            continue
        if current == previous:
            continue
        snap["release_decision"] = previous
        snapshots_repo.save_snapshot(conn, release_id, app_id, snap)


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
    """Apply a CICD request to the app-backed CICD fields. Returns app/task id."""
    # Work on a local copy so we can extract internal-only fields without
    # mutating the caller's dict and without exposing them to field validation.
    payload = dict(payload)
    # app_id is an internal-only field injected by cicd_first_new_app to link
    # the new task to its parent app on approval.  Regular user create requests
    # omit it (None) — submit_request validation already blocks unknown fields.
    linked_app_id: str | None = payload.pop("app_id", None) or None
    cicd_first_action = str(payload.pop(_CICD_FIRST_ACTION_FIELD, "") or "")
    cicd_first_target_decision = str(
        payload.pop(_CICD_FIRST_TARGET_DECISION_FIELD, "cicd_only") or "cicd_only"
    )
    decision_sync_release_id = str(payload.pop(_DECISION_SYNC_RELEASE_ID_FIELD, "") or "")
    decision_sync_target_decision = str(
        payload.pop(_DECISION_SYNC_TARGET_DECISION_FIELD, "") or ""
    )
    payload.pop(_DECISION_SYNC_ROLLBACK_FIELD, None)
    _validate_payload_fields(payload)
    if request_type == "create":
        app_id = linked_app_id or payload.get("app_id") or _task_id_to_app_id(conn, task_id)
        if app_id and apps_repo.get_app(conn, app_id):
            apps_repo.update_cicd_config(
                conn,
                app_id,
                {
                    "cicd_repo_type": payload.get("repo_type", ""),
                    "cicd_community_artifact": _community_app_value(payload.get("community_artifact")),
                    "cicd_build_image": payload.get("build_image", ""),
                    "cicd_test_timeout": payload.get("test_timeout", ""),
                    "cicd_notes": payload.get("notes", ""),
                },
            )
            cicd_repo.set_request_task_id(conn, req_id, app_id)
            if cicd_first_action == _CICD_FIRST_ACTION_CREATED:
                _activate_created_cicd_first_app(conn, app_id, cicd_first_target_decision)
            return app_id
        raise RuntimeError("CICD 创建申请缺少有效 app_id")
    else:
        # modify: apply diff — payload is {field: {old, new}}
        if not task_id:
            raise RuntimeError("修改请求缺少 task_id")
        app_id = _task_id_to_app_id(conn, task_id)
        if app_id:
            app_updates = {}
            git_url_update: str | None = None
            git_branch_update: str | None = None
            for field, change in payload.items():
                if field == "repo_name":
                    git_url_update = str(change.get("new") or "").strip()
                    continue
                if field == "branch":
                    git_branch_update = str(change.get("new") or "").strip()
                    continue
                app_field = _PAYLOAD_FIELD_TO_APP_CICD_FIELD.get(field)
                if not app_field:
                    continue
                new_value = change.get("new")
                if field == "community_artifact":
                    new_value = _community_app_value(new_value)
                app_updates[app_field] = new_value
            if git_url_update is not None or git_branch_update is not None:
                _update_app_git_identity(
                    conn,
                    app_id,
                    git_url=git_url_update,
                    git_branch=git_branch_update,
                )
            apps_repo.update_cicd_config(conn, app_id, app_updates)
            if decision_sync_release_id and decision_sync_target_decision:
                _apply_deferred_release_decision(
                    conn,
                    app_id,
                    decision_sync_release_id,
                    decision_sync_target_decision,
                )
            cicd_repo.set_request_task_id(conn, req_id, app_id)
            return app_id
        raise RuntimeError("CICD 修改申请缺少有效 app_id")


# ---------------------------------------------------------------------------
# Read functions
# ---------------------------------------------------------------------------


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
) -> list[dict]:
    """Return App-backed CICD rows with pending/delivery flags."""
    tasks = _app_cicd_tasks(conn)
    if status_filter and status_filter in CICD_STATUSES:
        tasks = [task for task in tasks if task["status"] == status_filter]
    return [_strip_task(t) for t in tasks]


def get_task_history(
    conn: sqlite3.Connection,
    task_id: str,
) -> list[dict]:
    """Return approved request history for an app-backed task id."""
    items = cicd_repo.list_requests(
        conn,
        task_id=task_id,
        status_filter="approved",
        approver_roles=CICD_APPROVER_ROLES,
    )
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
    source: str = "cicd_workbench",
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
    # Only decision-sync (sync_decision_to_cicd) writes status.
    if request_type == "modify" and "status" in (payload or {}):
        raise RuntimeError(
            "CICD 修改申请不允许直接修改运行状态；运行/停止由 App 决策驱动（Ruling A/D）"
        )
    if request_type == "modify" and source != "app_workbench":
        raise RuntimeError("CICD 配置修改请从 App 工作台的 CICD tab 提交")
    if request_type == "modify":
        app_id = _task_id_to_app_id(conn, task_id)
        if app_id:
            unsupported = set(payload or {}) - _APP_BACKED_MUTABLE_FIELDS
            if unsupported:
                fields = "、".join(sorted(unsupported))
                raise RuntimeError(
                    f"App 工作台 CICD tab 只能修改 App 表中的 CICD 配置字段；不支持：{fields}"
                )
        else:
            raise RuntimeError("CICD 修改申请必须关联有效 App")
    ts = beijing_timestamp()
    request_app_id = _task_id_to_app_id(conn, task_id) or (payload or {}).get("app_id")
    if request_app_id and not apps_repo.get_app(conn, str(request_app_id)):
        request_app_id = None
    if request_type == "create" and not request_app_id:
        raise RuntimeError("CICD 创建申请必须关联有效 App")
    with transaction(conn):
        req_id = cicd_repo.insert_request(
            conn,
            task_id=request_app_id or task_id,
            app_id=request_app_id,
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
                conn, req_id, payload, req.get("app_id") or req["task_id"], req["request_type"], ts
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
    req = dict(row)
    if req["status"] != "pending":
        raise RuntimeError(f"申请状态为 {req['status']}，无法拒绝")
    import json as _json

    payload = _json.loads(req["payload"] or "{}")
    if _is_release_decision_sync_stop_request(req, payload):
        raise RuntimeError("降停止由 App release 决策决定，CICD 审批不能拒绝")
    ts = beijing_timestamp()
    with transaction(conn):
        if _is_release_decision_sync_start_request(req, payload):
            _rollback_deferred_decision_sync(
                conn,
                str(req.get("app_id") or req.get("task_id") or ""),
                payload,
            )
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
    import json as _json

    payload = _json.loads(req["payload"] or "{}")
    if _is_release_decision_sync_stop_request(req, payload):
        raise RuntimeError("降停止由 App release 决策决定，CICD 申请不能取消")
    with transaction(conn):
        if _is_release_decision_sync_start_request(req, payload):
            _rollback_deferred_decision_sync(
                conn,
                str(req.get("app_id") or req.get("task_id") or ""),
                payload,
            )
        conn.execute(
            "UPDATE cicd_task_requests SET status='cancelled', reviewed_at=? WHERE id=?",
            (beijing_timestamp(), req_id),
        )
        row = conn.execute(
            "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
        ).fetchone()
    return _strip_request(dict(row))


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
            conn, req_id, payload, req.get("app_id") or req["task_id"], req["request_type"], ts
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
            conn, req_id, payload, req.get("app_id") or req["task_id"], req["request_type"], ts
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
    current_status_override: str | None = None,
    release_id: str | None = None,
    apply_release_decision_on_delivery: bool = False,
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
    if not apps_repo.get_app(conn, app_id):
        return None

    ts = beijing_timestamp()
    task_id = app_id
    current_status = current_status_override or (
        "Running" if target_status == "Stopped" else "Stopped"
    )

    if current_status == target_status:
        return None

    # Idempotent guard: skip if there is already a pending modify touching status.
    if (
        cicd_repo.has_open_modify_on_field(conn, app_id, "status")
    ):
        return None

    payload: dict = {"status": {"old": current_status, "new": target_status}}
    if apply_release_decision_on_delivery and release_id:
        payload[_DECISION_SYNC_RELEASE_ID_FIELD] = release_id
        payload[_DECISION_SYNC_TARGET_DECISION_FIELD] = release_decision
    req_id = cicd_repo.insert_request(
        conn,
        task_id=task_id,
        app_id=app_id,
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


def attach_decision_sync_rollback(
    conn: sqlite3.Connection,
    req_id: int,
    rollback_entries: list[dict],
) -> dict:
    """Attach rollback metadata to an internal decision-sync request payload."""
    import json as _json

    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    if not row:
        raise RuntimeError("申请不存在")
    req = dict(row)
    payload = _json.loads(req["payload"] or "{}")
    payload[_DECISION_SYNC_ROLLBACK_FIELD] = rollback_entries
    conn.execute(
        "UPDATE cicd_task_requests SET payload=? WHERE id=?",
        (_json.dumps(payload, ensure_ascii=False), req_id),
    )
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (req_id,)
    ).fetchone()
    return _strip_request(dict(row))


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


def _retryable_duplicate_create_info(
    conn: sqlite3.Connection,
    existing_app: dict,
    official_name: str,
) -> tuple[dict | None, str | None]:
    app_id = existing_app["id"]
    app_name = _app_display_name(conn, app_id, app_id)
    onboarding = cicd_first_onboarding_by_app(conn).get(app_id)
    onboarding_status = (onboarding or {}).get("cicd_onboarding_status", "")
    retryable = onboarding_status in {"rejected_create", "cancelled_create"}
    requested_name = (official_name or "").strip()
    display = app_name or app_id
    if retryable and requested_name and requested_name == display:
        return {
            "retry_existing_app_id": app_id,
            "retry_existing_app_name": display,
            "retry_onboarding_status": onboarding_status,
            "retry_review_note": (onboarding or {}).get("cicd_onboarding_review_note", ""),
            "retry_reviewed_at": (onboarding or {}).get("cicd_onboarding_reviewed_at", ""),
        }, None
    if retryable:
        return None, (
            f"该 Gerrit URL + branch 已存在 app（{display}），"
            f"请使用 {display} 名称重新申请，不能重复创建"
        )
    return None, (
        f"该 Gerrit URL + branch 已存在 app（{display}），"
        "请直接修改已有 app，不能重复创建"
    )


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
        """
        SELECT app_id, task_id, payload
        FROM cicd_task_requests
        WHERE request_type = 'create'
          AND (
            status = 'pending'
            OR delivery_status IN ('pending', 'returned')
          )
        """
    ).fetchall()
    for row in rows:
        if row["app_id"] == app_id or row["task_id"] == app_id:
            return True
        try:
            p = _json.loads(row["payload"] or "{}")
        except Exception:
            continue
        if p.get("app_id") == app_id:
            return True
    return False


def _has_active_cicd_create_for_app(
    conn: sqlite3.Connection,
    app_id: str,
) -> bool:
    """True when an app already has a non-terminal CICD create request."""
    import json as _json

    rows = conn.execute(
        "SELECT app_id, task_id, payload FROM cicd_task_requests "
        "WHERE request_type = 'create' AND status NOT IN ('rejected', 'cancelled')"
    ).fetchall()
    for row in rows:
        if row["app_id"] == app_id or row["task_id"] == app_id:
            return True
        try:
            p = _json.loads(row["payload"] or "{}")
        except Exception:
            continue
        if p.get("app_id") == app_id:
            return True
    return False


def _onboarding_status_for_request(row: dict) -> str:
    status = row.get("status") or ""
    delivery_status = row.get("delivery_status") or ""
    if status == "pending" or delivery_status in ("pending", "returned"):
        return "pending_create"
    if status == "rejected":
        return "rejected_create"
    if status == "cancelled":
        return "cancelled_create"
    return "active"


def cicd_first_onboarding_by_app(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return latest CICD-first create lifecycle state by app id.

    Only new code writes the explicit internal marker.  Unmarked historical
    create requests are ignored so we do not misclassify pre-existing orphan
    apps that were merely associated with CICD.
    """
    rows = conn.execute(
        """
        SELECT *
        FROM cicd_task_requests
        WHERE request_type = 'create'
          AND origin = 'cicd_workbench'
        ORDER BY submitted_at ASC, id ASC
        """
    ).fetchall()
    result: dict[str, dict] = {}
    for row in rows:
        raw = dict(row)
        payload = cicd_repo._load_payload(raw.get("payload") or "{}")
        if payload.get(_CICD_FIRST_ACTION_FIELD) != _CICD_FIRST_ACTION_CREATED:
            continue
        app_id = str(raw.get("app_id") or raw.get("task_id") or payload.get("app_id") or "").strip()
        if not app_id:
            continue
        result[app_id] = {
            "cicd_onboarding_status": _onboarding_status_for_request(raw),
            "cicd_onboarding_request_id": raw.get("id"),
            "cicd_onboarding_review_note": raw.get("review_note") or "",
            "cicd_onboarding_reviewed_at": raw.get("reviewed_at") or "",
            "cicd_onboarding_delivery_status": raw.get("delivery_status") or "",
        }
    return result


def attach_cicd_onboarding_state(
    conn: sqlite3.Connection,
    apps: list[dict],
) -> None:
    """Attach non-active CICD-first onboarding state to app dicts in-place."""
    by_app = cicd_first_onboarding_by_app(conn)
    for app in apps:
        state = by_app.get(app.get("id"))
        if not state or state.get("cicd_onboarding_status") == "active":
            continue
        app.update(state)


def _apply_onboarding_state_to_tasks(
    conn: sqlite3.Connection,
    tasks: list[dict],
) -> None:
    by_app = cicd_first_onboarding_by_app(conn)
    for task in tasks:
        state = by_app.get(task.get("app_id"))
        if not state or state.get("cicd_onboarding_status") == "active":
            continue
        task.update(state)
        task["status"] = "Stopped"


def _activate_created_cicd_first_app(
    conn: sqlite3.Connection,
    app_id: str,
    target_decision: str,
) -> None:
    """Make a CICD-first-created app active after its create request applies."""
    import release_system.core as core

    decision = core.normalize_release_decision(target_decision or "cicd_only")
    if decision not in {"release", "cicd_only", "stopped"}:
        decision = "cicd_only"
    for rel in core.list_releases(conn):
        if rel.get("released_locked"):
            continue
        snap = snapshots_repo.get_snapshot(conn, rel["id"], app_id)
        if not snap:
            continue
        if snap.get("release_decision") == decision:
            continue
        snap["release_decision"] = decision
        snapshots_repo.save_snapshot(conn, rel["id"], app_id, snap)


def preview_cicd_app_info(
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter_role: str,
    _fetch_fn=None,
) -> dict:
    """Fetch and parse Gerrit app_info for a CICD-first preview (NO DB writes).

    ALWAYS derives (git_url, git_branch) FIRST and RETURNS them even when the
    Gerrit content fetch fails (Wave-4 requirement: identity surfaced to wizard).

    Response shape — base (always present):
      git_url             str | None  — derived identity (None if manifest unresolvable)
      git_branch          str | None  — same
      needs_network       bool        — True for .xml manifest repos (identity needs network)
      app_info_unavailable bool       — True when the Gerrit content fetch failed
      app_info_error      str | None  — error detail when unavailable, else None

    Response shape — only when app_info_unavailable=False:
      app_version, x86_chips, arm_chips, python_label, pytorch_label, os, arch
      commit_id, parsed

    Args:
        repo_type:      advisory repo type (git/repo/manifest; dispatch by name shape)
        repo_name:      short repo name ('hpc_hpl') or .xml manifest path
        branch:         git branch / revision
        submitter_role: caller's role (auth check already done in router)
        _fetch_fn:      injectable fetch function for tests/e2e.  Signature:
                        (git_url: str, branch: str, **kwargs) -> (raw_json: str, commit_id: str)
                        Defaults to app.integrations.gerrit.fetch_app_info.
                        Use make_fake_app_info_fetch() for offline tests.

    Raises:
        ValueError: repo_name is empty (caller input error → HTTP 400).
                    All other failure modes (manifest unresolvable, Gerrit unreachable)
                    are returned as soft flags in the response dict, NOT exceptions.
    """
    import release_system.core as core

    from app.config import settings
    from app.identity import repo_to_git_identity

    # Guard: empty repo_name / branch are always caller-input errors (→ 400).
    if not (repo_name or "").strip():
        raise ValueError("repo_name 不能为空，请检查 repo_name / branch")
    if not (branch or "").strip():
        raise ValueError("branch 不能为空，请检查 repo_name / branch")

    # Determine if this is a manifest repo (identity needs network).
    is_manifest = (repo_name or "").strip().endswith(".xml")

    # Step 1: Derive identity FIRST.
    # For git-type short names this is offline and always succeeds.
    # For .xml manifests it requires network; returns (None, None) on failure.
    git_url, git_branch = repo_to_git_identity(repo_type, repo_name, branch)

    base: dict = {
        "git_url": git_url,
        "git_branch": git_branch,
        "needs_network": is_manifest,
    }

    if not git_url or not git_branch:
        # Manifest identity resolution failed (network unreachable).
        # Return what we have — identity is unresolved but not a fatal error.
        return {
            **base,
            "app_info_unavailable": True,
            "app_info_error": (
                "manifest 路径需要联网解析（sw-gerrit-devops:29418 不可达）"
                f"，无法确定 repo 身份 ({repo_name})"
            ),
        }

    # Step 2: Fetch Gerrit content (may fail if Gerrit is unreachable).
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
        # Identity was resolved but content fetch failed.
        # Return identity + soft unavailable flag — wizard can still show the mapping.
        return {
            **base,
            "app_info_unavailable": True,
            "app_info_error": str(exc),
        }

    # Happy path: identity resolved + content fetched.
    parsed = core.parse_app_info(raw_json)

    return {
        **base,
        "app_info_unavailable": False,
        "app_info_error": None,
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


def preview_cicd_app_info_for_create(
    conn: sqlite3.Connection,
    *,
    repo_type: str,
    repo_name: str,
    branch: str,
    submitter_role: str,
    official_name: str = "",
    _fetch_fn=None,
) -> dict:
    """DB-aware fetch-preview for the new-app wizard.

    This keeps the read-only preview behavior, but rejects an already-known
    app identity before contacting Gerrit.  Users get the duplicate error when
    they click "拉取" instead of waiting for Gerrit and only failing at create.
    """
    from app.identity import repo_to_git_identity

    if submitter_role not in CICD_CREATE_ROLES:
        raise PermissionError("只有 Owner、RM 可以预览 Gerrit app_info")
    if not (repo_name or "").strip():
        raise ValueError("repo_name 不能为空，请检查 repo_name / branch")
    if not (branch or "").strip():
        raise ValueError("branch 不能为空，请检查 repo_name / branch")

    raw_repo_name = repo_name.strip()
    raw_branch = branch.strip()
    retry_info: dict | None = None

    # First reject exact/raw identities.  Repo-type apps may store the manifest
    # path itself (for example APP/lammps/...xml @ master); checking this before
    # manifest resolution avoids waiting on Gerrit only to discover a duplicate.
    existing_app = _find_app_by_identity(conn, raw_repo_name, raw_branch)
    if existing_app:
        retry_info, message = _retryable_duplicate_create_info(conn, existing_app, official_name)
        if message:
            raise RuntimeError(message)

    git_url, git_branch = repo_to_git_identity(repo_type, raw_repo_name, raw_branch)
    if git_url and git_branch:
        existing_app = _find_app_by_identity(conn, git_url, git_branch)
        if existing_app:
            retry_info, message = _retryable_duplicate_create_info(conn, existing_app, official_name)
            if message:
                raise RuntimeError(message)

    result = preview_cicd_app_info(
        repo_type=repo_type,
        repo_name=repo_name,
        branch=branch,
        submitter_role=submitter_role,
        _fetch_fn=_fetch_fn,
    )
    if retry_info:
        result.update(retry_info)
    return result


def make_fake_app_info_fetch(
    *,
    app_name: str = "fake-app",
    app_version: str = "1.0.0-fake",
    x86_chips: "list[str] | None" = None,
    arm_chips: "list[str] | None" = None,
    python_label: str = "3.10",
    pytorch_label: str = "2.1",
    os_label: str = "ubuntu22.04",
    arch: str = "amd64",
    commit_id: str = "fakefake0000000000000000000000000000fake",
    extra_build_fields: "dict | None" = None,
    extra_root_fields: "dict | None" = None,
):
    """Return a fake _fetch_fn for tests/e2e — no network required.

    The returned function has signature:
        (git_url: str, branch: str, **kwargs) -> (raw_json: str, commit_id: str)

    It returns a realistic but fabricated app_info.json payload that
    core.parse_app_info() can parse into the 7 preview fields.

    Usage (direct service call)::

        fetch = cicd_service.make_fake_app_info_fetch(app_version="2.0")
        preview = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name="hpc_myapp",
            branch="main",
            submitter_role="RM",
            _fetch_fn=fetch,
        )

    Usage (HTTP test — patch at module level)::

        from unittest.mock import patch
        fake = cicd_service.make_fake_app_info_fetch()
        with patch("app.integrations.gerrit.fetch_app_info", fake):
            resp = client.post("/api/cicd/apps/fetch-preview", json=body)
    """
    import json as _json

    _x86 = x86_chips if x86_chips is not None else ["C500", "N100"]
    _arm = arm_chips if arm_chips is not None else []
    build_key = f"{os_label}_{arch}"
    build_env: dict = {
        "arch": arch,
        "supported_chip": _x86,
        "enabled": True,
        "python_label": python_label,
        "pytorch_label": pytorch_label,
        "os": os_label,
        **(extra_build_fields or {}),
    }

    data: dict = {
        "app_name": app_name,
        "app_version": app_version,
        "app_build": {build_key: build_env},
        **(extra_root_fields or {}),
    }
    if _arm:
        arm_key = f"{os_label}_arm64"
        data["app_build"][arm_key] = {
            **build_env,
            "arch": "arm64",
            "supported_chip": _arm,
        }

    _raw = _json.dumps(data, ensure_ascii=False)
    _cid = commit_id

    def _fake(git_url: str, branch: str, **kwargs) -> "tuple[str, str]":
        return (_raw, _cid)

    return _fake


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
      - app row + initial snapshot(stopped) in all unlocked releases
        (OR: locate an existing app by canonical identity, skip creation)
      - optional app_info attachment to all unlocked snapshots (when
        app_info_parsed is provided — sets owner_confirmed=True)
      - pending CICD 'create' request (Ruling B: always pending)

    The app-backed CICD row only becomes active when the create request applies
    (immediate approval or SPD delivery). app_id is embedded in the request
    payload so _apply_cicd_request links the request back to its parent app.

    1:1 cardinality ruling:
      - derived identity matches existing app with an active CICD create flow
        → reject "该 app 已有 CICD 任务"
      - derived identity matches existing app without an active CICD create flow
        → create pending create request for that app
      - no existing app → create new app + initial stopped snapshots

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
            request_app_name = _app_display_name(conn, app_id, official_name)
            if request_app_name.strip() and request_app_name.strip() != official_name:
                raise RuntimeError(
                    f"该 Gerrit URL + branch 已存在 app（{request_app_name}），"
                    f"请使用 {request_app_name} 名称重新申请，不能用新名称重复创建"
                )
            onboarding = cicd_first_onboarding_by_app(conn).get(app_id)
            onboarding_status = (onboarding or {}).get("cicd_onboarding_status", "")
            if onboarding_status == "pending_create":
                raise RuntimeError(
                    f"该 app（{app_id}）已有待审批或待交付的 CICD 创建申请，请等待处理完成"
                )
            # Idempotency guard: pending create already waiting for approval?
            if _has_pending_cicd_create_for_app(conn, app_id):
                raise RuntimeError(
                    f"该 app（{app_id}）已有待审批的 CICD 创建申请，请等待 RM 审批"
                )
            if onboarding_status == "active" or _has_active_cicd_create_for_app(conn, app_id):
                raise RuntimeError(
                    f"该 app（{app_id}）已有 CICD 创建申请生效，无法重复创建"
                )
            if onboarding_status in {"rejected_create", "cancelled_create"}:
                action = "associated"
                cicd_first_action = _CICD_FIRST_ACTION_CREATED
            else:
                action = "associated"
                cicd_first_action = _CICD_FIRST_ACTION_ASSOCIATED

        else:
            # ---- No existing app: create new app + initial stopped snapshots ----
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
                release_decision="stopped",
                owner=submitter,
                doc_target="manual",
            )
            action = "created"
            cicd_first_action = _CICD_FIRST_ACTION_CREATED
            request_app_name = official_name

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

        apps_repo.update_cicd_config(
            conn,
            app_id,
            {
                "cicd_repo_type": payload.get("cicd_repo_type", ""),
                "cicd_community_artifact": payload.get("cicd_community_artifact", ""),
                "cicd_build_image": payload.get("cicd_build_image", ""),
                "cicd_test_timeout": payload.get("cicd_test_timeout", ""),
                "cicd_notes": payload.get("cicd_notes", ""),
            },
        )

        # ---- Create pending CICD 'create' request (Ruling B: always pending) ----
        # After the app-backed cutover, app_id is the canonical request relation
        # and task_id stores the same app id for the existing API field.
        create_payload: dict = {
            "app_name": request_app_name,
            "app_id": app_id,                            # internal linkage field
            _CICD_FIRST_ACTION_FIELD: cicd_first_action,
            _CICD_FIRST_TARGET_DECISION_FIELD: "cicd_only",
            "repo_type": repo_type,
            "repo_name": repo_name,
            "branch": branch,
            "app_version": payload.get("app_version", ""),
            "build_product": payload.get("build_product", []),
            "community_artifact": payload.get("community_artifact", []),
            "build_image": payload.get("build_image", ""),
            "test_timeout": _test_timeout_value(payload.get("test_timeout")),
            "owner_username": submitter,
            "status": "Running",  # initial task status — aligned with cicd_only decision
            "notes": payload.get("notes", ""),
        }

        ts = beijing_timestamp()
        req_id = cicd_repo.insert_request(
            conn,
            task_id=app_id,
            app_id=app_id,
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
