"""CICD repository — cicd_tasks and cicd_task_requests table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.

Key Phase 0/1 additions:
  - create_task() has app_id as a first-class parameter (plan §4.1)
  - find_tasks_by_identity() for repo (repo_name, branch) lookup
  - has_open_modify_on_field() for idempotent sync guard (plan P4 / §3.5 b+)
  - list_requests() takes since_cutoff as a pre-computed datetime string
    (NOT SQL datetime('now') — fixes DA C5)
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

# ---------------------------------------------------------------------------
# Valid payload fields (mirrors core.py:CICD_TASK_FIELDS).
# status is intentionally EXCLUDED — only decision-sync + abandon can write it.
# ---------------------------------------------------------------------------
CICD_TASK_MUTABLE_FIELDS: frozenset[str] = frozenset(
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
        "notes",
    }
)

# Fields that decision-sync and abandon endpoints may write
CICD_STATUS_FIELDS: frozenset[str] = frozenset({"status"})


# ---------------------------------------------------------------------------
# Row shaping — mirrors core.py:_cicd_task_row
# ---------------------------------------------------------------------------

def _task_row(row: sqlite3.Row) -> dict[str, Any]:
    d = row_to_dict(row)
    try:
        d["build_product"] = json.loads(d.get("build_product") or "[]")
    except Exception:
        d["build_product"] = []
    try:
        d["community_artifact"] = json.loads(d.get("community_artifact") or "[]")
    except Exception:
        d["community_artifact"] = []
    return d


def _request_row(row: sqlite3.Row) -> dict[str, Any]:
    d = row_to_dict(row)
    d["payload"] = _load_payload(d.get("payload") or "{}")
    return d


def _load_payload(raw: str) -> dict:
    try:
        payload = json.loads(raw or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# ID generation — mirrors core.py:_next_cicd_id
# ---------------------------------------------------------------------------

def next_cicd_id(conn: sqlite3.Connection) -> str:
    """Generate the next CICD-NNNN id by inspecting the existing max."""
    row = conn.execute("SELECT id FROM cicd_tasks ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return "CICD-0001"
    last = row["id"]  # e.g. "CICD-0042"
    try:
        num = int(last.split("-", 1)[1]) + 1
    except (IndexError, ValueError):
        num = 1
    return f"CICD-{num:04d}"


# ---------------------------------------------------------------------------
# Task reads
# ---------------------------------------------------------------------------

def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    """Return a cicd_task row (with parsed JSON fields) or None."""
    row = conn.execute("SELECT * FROM cicd_tasks WHERE id = ?", (task_id,)).fetchone()
    return _task_row(row) if row else None


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return all cicd_tasks, optionally filtered by status."""
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM cicd_tasks WHERE status = ? ORDER BY id",
            (status_filter,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cicd_tasks ORDER BY id").fetchall()
    return [_task_row(r) for r in rows]


def tasks_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    """Return cicd_tasks linked to app_id (should be 0 or 1 under 1:1 cardinality)."""
    rows = conn.execute(
        "SELECT * FROM cicd_tasks WHERE app_id = ? ORDER BY id",
        (app_id,),
    ).fetchall()
    return [_task_row(r) for r in rows]


def find_tasks_by_identity(
    conn: sqlite3.Connection,
    repo_name: str,
    branch: str,
) -> list[dict[str, Any]]:
    """Find cicd_tasks by (repo_name, branch) identity — used by migration and CICD-first.

    Uses idx_cicd_tasks_repo index.
    """
    rows = conn.execute(
        "SELECT * FROM cicd_tasks WHERE repo_name = ? AND branch = ? ORDER BY id",
        (repo_name, branch),
    ).fetchall()
    return [_task_row(r) for r in rows]


def pending_task_ids(conn: sqlite3.Connection) -> set[str]:
    """Return set of task_ids that have at least one pending request."""
    return {
        r["task_id"]
        for r in conn.execute(
            "SELECT DISTINCT task_id FROM cicd_task_requests "
            "WHERE status = 'pending' AND task_id IS NOT NULL"
        ).fetchall()
    }


def delivery_pending_task_ids(conn: sqlite3.Connection) -> set[str]:
    """Return set of task_ids with pending or returned deliveries."""
    return {
        r["task_id"]
        for r in conn.execute(
            "SELECT DISTINCT task_id FROM cicd_task_requests "
            "WHERE delivery_status IN ('pending', 'returned') AND task_id IS NOT NULL"
        ).fetchall()
    }


def has_open_modify_on_field(
    conn: sqlite3.Connection,
    task_id: str,
    field: str,
) -> bool:
    """True if there is already an open (pending/dispatched) modify request touching *field*.

    Used as an idempotent guard: if a decision-sync already produced a pending
    modify request for 'status', a second call with the same intent is a no-op.
    """
    rows = conn.execute(
        "SELECT payload FROM cicd_task_requests "
        "WHERE task_id = ? AND request_type = 'modify' AND status = 'pending'",
        (task_id,),
    ).fetchall()
    for row in rows:
        p = _load_payload(row["payload"])
        if field in p:
            return True
    return False


def task_mini_info(
    conn: sqlite3.Connection,
    task_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return {task_id: mini_dict} with just display fields (for request list join).

    Mirrors core.py:_attach_cicd_request_task_info.
    """
    if not task_ids:
        return {}
    rows = conn.execute(
        "SELECT id, app_name, app_version, repo_name, branch, status "
        "FROM cicd_tasks WHERE id IN ({})".format(",".join("?" * len(task_ids))),
        task_ids,
    ).fetchall()
    return {r["id"]: row_to_dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Task writes
# ---------------------------------------------------------------------------

def create_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    app_name: str,
    app_id: str | None,
    app_version: str = "",
    repo_type: str = "git",
    repo_name: str = "",
    branch: str = "",
    build_product: list | None = None,
    community_artifact: list | None = None,
    build_image: str = "",
    test_timeout: int = 40,
    owner_username: str,
    status: str = "Running",
    notes: str = "",
    created_at: str,
    updated_at: str,
) -> None:
    """Insert a new cicd_task row.  app_id is a first-class parameter."""
    conn.execute(
        """
        INSERT INTO cicd_tasks
          (id, app_name, app_id, app_version, repo_type, repo_name, branch,
           build_product, community_artifact, build_image, test_timeout,
           owner_username, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            app_name,
            app_id,
            app_version,
            repo_type,
            repo_name,
            branch,
            json.dumps(build_product or [], ensure_ascii=False),
            json.dumps(community_artifact or [], ensure_ascii=False),
            build_image,
            int(test_timeout),
            owner_username,
            status,
            notes,
            created_at,
            updated_at,
        ),
    )


def apply_modify_fields(
    conn: sqlite3.Connection,
    task_id: str,
    fields: dict[str, Any],
    *,
    updated_at: str,
) -> None:
    """Apply a field→new_value dict to a cicd_task row.

    Handles JSON serialization for list fields.
    Only CICD_TASK_MUTABLE_FIELDS and CICD_STATUS_FIELDS are allowed;
    caller is responsible for validating the field set before calling this.
    """
    for field, new_val in fields.items():
        if field == "build_product":
            new_val = json.dumps(new_val or [], ensure_ascii=False)
        elif field == "community_artifact":
            new_val = json.dumps(new_val or [], ensure_ascii=False)
        elif field == "test_timeout":
            new_val = int(new_val or 40)
        conn.execute(
            f"UPDATE cicd_tasks SET {field} = ?, updated_at = ? WHERE id = ?",
            (new_val, updated_at, task_id),
        )


def update_task_app_id(
    conn: sqlite3.Connection,
    task_id: str,
    app_id: str | None,
    *,
    updated_at: str,
) -> None:
    """Set (or clear) app_id on a cicd_task."""
    conn.execute(
        "UPDATE cicd_tasks SET app_id = ?, updated_at = ? WHERE id = ?",
        (app_id, updated_at, task_id),
    )


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    """Delete a cicd_task and all its request history."""
    conn.execute("DELETE FROM cicd_task_requests WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM cicd_tasks WHERE id = ?", (task_id,))


# ---------------------------------------------------------------------------
# Request reads
# ---------------------------------------------------------------------------

def get_request(conn: sqlite3.Connection, request_id: int) -> dict[str, Any] | None:
    """Return a cicd_task_request row (with parsed payload) or None."""
    row = conn.execute(
        "SELECT * FROM cicd_task_requests WHERE id = ?", (request_id,)
    ).fetchone()
    return _request_row(row) if row else None


def list_requests(
    conn: sqlite3.Connection,
    *,
    username: str | None = None,
    role: str = "Owner",
    task_id: str | None = None,
    status_filter: str | None = None,
    since_cutoff: str | None = None,  # pre-computed Beijing timestamp (DA C5 fix)
    exclude_cancelled: bool = False,
    approver_roles: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Return cicd_task_requests with flexible filters.

    since_cutoff must be a pre-computed naive-Beijing timestamp string from the
    caller (e.g. beijing_timestamp() offset by N days).  Do NOT use SQL
    datetime('now') here — that would embed UTC (DA C5 fix).

    approver_roles: set of roles that can see all records; defaults to {"RM"}.
    """
    if approver_roles is None:
        approver_roles = frozenset({"RM"})

    clauses: list[str] = []
    params: list[Any] = []

    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)

    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)

    if exclude_cancelled and role not in approver_roles:
        clauses.append("(status != 'cancelled' OR submitter = ?)")
        params.append(username or "")

    if username and role not in approver_roles:
        clauses.append("submitter = ?")
        params.append(username)
    elif username:
        clauses.append("submitter = ?")
        params.append(username)

    if since_cutoff:
        clauses.append("submitted_at >= ?")
        params.append(since_cutoff)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM cicd_task_requests {where} ORDER BY submitted_at DESC",
        params,
    ).fetchall()
    return [_request_row(r) for r in rows]


def list_deliveries(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
    submitter: str | None = None,
) -> list[dict[str, Any]]:
    """List requests that went through the dispatch_spd delivery workflow."""
    clauses = ["approval_mode = 'dispatch_spd'"]
    params: list[Any] = []

    if status_filter == "pending_or_returned":
        clauses.append("delivery_status IN ('pending', 'returned')")
    elif status_filter:
        clauses.append("delivery_status = ?")
        params.append(status_filter)

    if submitter:
        clauses.append("submitter = ?")
        params.append(submitter)

    where = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM cicd_task_requests {where} ORDER BY reviewed_at DESC",
        params,
    ).fetchall()
    return [_request_row(r) for r in rows]


def task_history(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    """Return all approved requests for a task in chronological order."""
    rows = conn.execute(
        "SELECT * FROM cicd_task_requests "
        "WHERE task_id = ? AND status = 'approved' ORDER BY reviewed_at ASC",
        (task_id,),
    ).fetchall()
    return [_request_row(r) for r in rows]


def notification_counts(
    conn: sqlite3.Connection,
    username: str,
    role: str,
    *,
    approver_roles: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return notification badge data for a user.

    Mirrors core.py:get_cicd_notifications (read path only).
    """
    if approver_roles is None:
        approver_roles = frozenset({"RM"})

    row = conn.execute(
        "SELECT last_visited_at FROM cicd_notifications WHERE username = ?",
        (username,),
    ).fetchone()
    last_visited = row["last_visited_at"] if row else ""

    if role == "SPD":
        count = conn.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE delivery_status = 'pending'"
        ).fetchone()[0]
    elif role in approver_roles:
        pending = conn.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE status = 'pending'"
        ).fetchone()[0]
        returned = conn.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE delivery_status = 'returned'"
        ).fetchone()[0]
        count = pending + returned
    else:
        if last_visited:
            count = conn.execute(
                """
                SELECT COUNT(*) FROM cicd_task_requests
                WHERE submitter = ? AND status IN ('approved', 'rejected')
                AND reviewed_at > ?
                """,
                (username, last_visited),
            ).fetchone()[0]
        else:
            count = 0

    return {"count": count, "last_visited_at": last_visited}


# ---------------------------------------------------------------------------
# Request writes
# ---------------------------------------------------------------------------

def insert_request(
    conn: sqlite3.Connection,
    *,
    task_id: str | None,
    request_type: str,
    payload: dict,
    submitter: str,
    submitter_display: str = "",
    submitted_at: str,
    status: str = "pending",
    reviewer: str = "",
    reviewed_at: str = "",
    review_note: str = "",
    is_self_approved: int = 0,
    approval_mode: str = "immediate",
    origin: str = "cicd_workbench",
) -> int:
    """Insert a cicd_task_request row; return the new row id."""
    conn.execute(
        """
        INSERT INTO cicd_task_requests
          (task_id, request_type, payload, submitter, submitter_display,
           submitted_at, status, reviewer, reviewed_at, review_note,
           is_self_approved, approval_mode, origin)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            request_type,
            json.dumps(payload, ensure_ascii=False),
            submitter,
            submitter_display,
            submitted_at,
            status,
            reviewer,
            reviewed_at,
            review_note,
            is_self_approved,
            approval_mode,
            origin,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def set_request_task_id(conn: sqlite3.Connection, request_id: int, task_id: str) -> None:
    """Back-fill task_id on a request row after task creation."""
    conn.execute(
        "UPDATE cicd_task_requests SET task_id = ? WHERE id = ?",
        (task_id, request_id),
    )


def update_request(
    conn: sqlite3.Connection,
    request_id: int,
    **fields: Any,
) -> None:
    """Update arbitrary columns on a cicd_task_request row."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = [*fields.values(), request_id]
    conn.execute(
        f"UPDATE cicd_task_requests SET {set_clause} WHERE id = ?",
        params,
    )


def mark_notification_visited(conn: sqlite3.Connection, username: str, ts: str) -> None:
    """Update last_visited_at for a user's notification badge."""
    conn.execute(
        """
        INSERT INTO cicd_notifications(username, last_visited_at)
        VALUES (?, ?)
        ON CONFLICT(username) DO UPDATE SET last_visited_at=excluded.last_visited_at
        """,
        (username, ts),
    )
