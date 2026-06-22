"""CICD repository — cicd_task_requests table CRUD.

Convention: pure functions fn(conn, ...), SQL only, no business rules.

After the app-backed CICD cutover, apps are the CICD task source of truth.
This repository intentionally keeps request and notification persistence only.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.repositories.base import row_to_dict

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


def pending_task_ids(conn: sqlite3.Connection) -> set[str]:
    """Return set of task_ids that have at least one pending request."""
    return {
        r["app_id"] or r["task_id"]
        for r in conn.execute(
            "SELECT DISTINCT COALESCE(app_id, task_id) AS id, app_id, task_id "
            "FROM cicd_task_requests "
            "WHERE status = 'pending' AND COALESCE(app_id, task_id) IS NOT NULL"
        ).fetchall()
    }


def delivery_pending_task_ids(conn: sqlite3.Connection) -> set[str]:
    """Return set of task_ids with pending or returned deliveries."""
    return {
        r["app_id"] or r["task_id"]
        for r in conn.execute(
            "SELECT DISTINCT COALESCE(app_id, task_id) AS id, app_id, task_id "
            "FROM cicd_task_requests "
            "WHERE delivery_status IN ('pending', 'returned') "
            "AND COALESCE(app_id, task_id) IS NOT NULL"
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
        "WHERE COALESCE(app_id, task_id) = ? "
        "AND request_type = 'modify' AND status = 'pending'",
        (task_id,),
    ).fetchall()
    for row in rows:
        p = _load_payload(row["payload"])
        if field in p:
            return True
    return False


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
        clauses.append("COALESCE(app_id, task_id) = ?")
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
            """
            SELECT COUNT(*)
            FROM cicd_task_requests r
            JOIN apps a ON a.id = r.app_id
            WHERE r.delivery_status = 'pending'
            """
        ).fetchone()[0]
    elif role in approver_roles:
        pending = conn.execute(
            """
            SELECT COUNT(*)
            FROM cicd_task_requests r
            JOIN apps a ON a.id = r.app_id
            WHERE r.status = 'pending'
            """
        ).fetchone()[0]
        returned = conn.execute(
            """
            SELECT COUNT(*)
            FROM cicd_task_requests r
            JOIN apps a ON a.id = r.app_id
            WHERE r.delivery_status = 'returned'
            """
        ).fetchone()[0]
        count = pending + returned
    else:
        if last_visited:
            count = conn.execute(
                """
                SELECT COUNT(*) FROM cicd_task_requests
                WHERE app_id IN (SELECT id FROM apps)
                AND submitter = ? AND status IN ('approved', 'rejected')
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
    app_id: str | None = None,
) -> int:
    """Insert a cicd_task_request row; return the new row id."""
    conn.execute(
        """
        INSERT INTO cicd_task_requests
          (task_id, app_id, request_type, payload, submitter, submitter_display,
           submitted_at, status, reviewer, reviewed_at, review_note,
           is_self_approved, approval_mode, origin)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            app_id,
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
