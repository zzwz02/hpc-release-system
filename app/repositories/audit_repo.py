"""Audit repository — audit table writes and queries.

Convention: pure functions fn(conn, ...), SQL only, no business rules.
detail serialization (dict → JSON string) is the responsibility of the caller
(services/authz), consistent with core.py:audit().
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import dumps_json, loads_json, row_to_dict


def _parse_detail(raw: Any) -> list:
    """Best-effort JSON parse of an audit detail field.

    Returns [] for empty/None/non-JSON values so the caller always gets a list.
    """
    if not raw:
        return []
    try:
        return loads_json(raw, [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def log_audit(
    conn: sqlite3.Connection,
    message: str,
    *,
    ts: str,
    user: str = "system",
    role: str = "system",
    app_id: str = "",
    release_id: str = "",
    event: str = "",
    detail: Any = "",
) -> None:
    """Append an audit log entry.

    *detail* may be a string (stored as-is) or any JSON-serializable value.
    """
    detail_text = detail if isinstance(detail, str) else dumps_json(detail)
    conn.execute(
        "INSERT INTO audit(ts, user, role, app_id, release_id, event, message, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, user, role, app_id, release_id, event, message, detail_text),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def app_audit_log(
    conn: sqlite3.Connection,
    app_id: str,
    release_id: str = "",
) -> list[dict[str, Any]]:
    """Return audit entries for an app (optionally filtered to one release).

    Mirrors core.py:app_audit_log.
    """
    cols = (
        "SELECT ts, user, role, release_id, event, message, detail "
        "FROM audit WHERE app_id = ?"
    )
    if release_id:
        rows = conn.execute(
            cols + " AND release_id = ? ORDER BY id DESC", (app_id, release_id)
        )
    else:
        rows = conn.execute(cols + " ORDER BY id DESC", (app_id,))

    entries = []
    for row in rows:
        entry = row_to_dict(row)
        entry["detail"] = _parse_detail(entry.get("detail"))
        entries.append(entry)
    return entries


def release_audit_log(
    conn: sqlite3.Connection,
    release_id: str,
    event: str | None = None,
) -> list[dict[str, Any]]:
    """Return audit entries for a release, optionally filtered by event."""
    if event:
        rows = conn.execute(
            "SELECT ts, user, role, app_id, event, message, detail "
            "FROM audit WHERE release_id = ? AND event = ? ORDER BY id DESC",
            (release_id, event),
        )
    else:
        rows = conn.execute(
            "SELECT ts, user, role, app_id, event, message, detail "
            "FROM audit WHERE release_id = ? ORDER BY id DESC",
            (release_id,),
        )
    entries = []
    for row in rows:
        entry = row_to_dict(row)
        entry["detail"] = _parse_detail(entry.get("detail"))
        entries.append(entry)
    return entries


def qa_audit_logs_by_app(
    conn: sqlite3.Connection,
    release_id: str,
    app_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return QA status-change audit entries grouped by app_id.

    Mirrors core.py:release_qa_audit_logs (inner query only).
    """
    if not app_ids:
        return {}
    placeholders = ",".join("?" for _ in app_ids)
    params: list[Any] = [release_id, "qa_set_status", *app_ids]
    rows = conn.execute(
        f"""
        SELECT ts, user, role, app_id, event, message, detail
        FROM audit
        WHERE release_id = ? AND event = ? AND app_id IN ({placeholders})
        ORDER BY id DESC
        """,
        params,
    )
    result: dict[str, list[dict[str, Any]]] = {aid: [] for aid in app_ids}
    for row in rows:
        entry = row_to_dict(row)
        entry["detail"] = _parse_detail(entry.get("detail"))
        result.setdefault(entry["app_id"], []).append(entry)
    return result


def clear_all_audit(conn: sqlite3.Connection) -> None:
    """Delete all audit rows (used by clear_business_data)."""
    conn.execute("DELETE FROM audit")
