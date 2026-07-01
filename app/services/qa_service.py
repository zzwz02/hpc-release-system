"""QA service — status batch updates, log upload/download, and LLM analysis.

Most orchestration delegates to release_system.core.  QA status updates live
here because FastAPI has runtime-only release-note rules that intentionally
differ from the frozen legacy reference.
"""
from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any

import release_system.core as core
from app.db.connection import transaction
from app.repositories.audit_repo import log_audit
from app.repositories.snapshots_repo import save_snapshot
from app.timeutil import beijing_timestamp

_ISSUE_NOTE_REQUIRED_STATUSES = {"has_issues", "cannot_release"}
_QA_STATUS_LABELS = {
    "has_issues": "存在问题",
    "cannot_release": "不可发布",
}

# ---------------------------------------------------------------------------
# QA status batch update — POST /api/qa/status-batch
# ---------------------------------------------------------------------------

def set_qa_status_batch(
    conn: sqlite3.Connection,
    release_id: str,
    items: list[dict],
    *,
    user: str,
    role: str,
) -> dict:
    """Apply several QA-status updates atomically.

    ``cannot_release`` follows the same note requirement as ``has_issues``.
    Returns {"ok": True, "updated": n}.
    """
    release = core.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改 QA 状态")

    # (app_id, snapshot, status, issue_note, old_status, old_note)
    prepared: list[tuple[str, dict[str, Any], str, str, str, str]] = []
    errors: list[str] = []
    for item in items:
        app_id = item.get("app_id", "")
        status = item.get("status", "")
        issue_note = (item.get("issue_note") or "").strip()
        if status not in core.QA_STATUSES:
            errors.append(f"{app_id}：无效的 QA 状态 {status!r}")
            continue
        snapshot = release["snapshots"].get(app_id)
        if not snapshot:
            errors.append(f"{app_id}：不在本 release 中")
            continue
        if snapshot.get("release_decision") != "release":
            errors.append(f"{app_id}：仅 release 决策的 app 可标注 QA 状态")
            continue
        if status in _ISSUE_NOTE_REQUIRED_STATUSES and not issue_note:
            label = _QA_STATUS_LABELS.get(status, status)
            errors.append(f"{app_id}：标注「{label}」时必须填写问题说明")
            continue
        prepared.append((
            app_id,
            snapshot,
            status,
            issue_note,
            snapshot.get("qa_status", "not_checked"),
            snapshot.get("qa_issue_note", ""),
        ))

    if errors:
        raise ValueError("；".join(errors))

    ts = beijing_timestamp()
    with transaction(conn):
        for app_id, snapshot, status, issue_note, old_status, old_note in prepared:
            snapshot["qa_status"] = status
            snapshot["qa_issue_note"] = (
                issue_note if status in _ISSUE_NOTE_REQUIRED_STATUSES else ""
            )
            save_snapshot(conn, release_id, app_id, snapshot)
            detail = [{"field": "qa_status", "label": "QA 状态", "old": old_status, "new": status}]
            if old_note or snapshot["qa_issue_note"]:
                detail.append({
                    "field": "qa_issue_note",
                    "label": "问题说明",
                    "old": old_note,
                    "new": snapshot["qa_issue_note"],
                })
            log_audit(
                conn,
                f"QA 标注 {app_id} 为 {status}" + (f"：{issue_note}" if issue_note else ""),
                ts=ts,
                user=user,
                role=role,
                app_id=app_id,
                release_id=release_id,
                event="qa_set_status",
                detail=detail,
            )
    updated = {app_id: snapshot for app_id, snapshot, *_ in prepared}
    return {"ok": True, "updated": len(updated)}


# ---------------------------------------------------------------------------
# QA log upload — POST /api/qa/upload-log
# ---------------------------------------------------------------------------

def upload_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    content_b64: str,
    filename: str,
    db_path: Path,
    user: str,
    role: str,
) -> dict:
    """Decode and store a base64-encoded QA log.

    Mirrors server.py:954-971.  Returns {"ok": True, **meta}.
    """
    if not content_b64:
        raise ValueError("content_base64 required")
    content = base64.b64decode(content_b64)
    meta = core.qa_upload_log(
        conn,
        db_path,
        release_id,
        content,
        filename,
        user=user,
        role=role,
    )
    return {"ok": True, **meta}


# ---------------------------------------------------------------------------
# QA log download — GET /api/qa-log/download
# ---------------------------------------------------------------------------

def get_qa_log_download(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[bytes, str]:
    """Return (file_bytes, filename) for a QA log download.

    Mirrors server.py:416-433.
    Raises RuntimeError (→ 400) when no log or file is missing.
    The router converts these to the right HTTP responses.
    """
    if not release_id:
        raise ValueError("release_id is required")
    meta = core.get_qa_log(conn, release_id)
    if not meta:
        # Old server sends HTTP 404 directly; service raises RuntimeError so
        # the router can map it to a 404 Response.
        raise RuntimeError("no qa log")
    path = Path(meta["storage_path"])
    if not path.exists():
        raise RuntimeError("qa log file missing")
    return path.read_bytes(), meta["filename"]


# ---------------------------------------------------------------------------
# QA reports — GET /api/qa-reports
# ---------------------------------------------------------------------------

def get_qa_reports(
    conn: sqlite3.Connection,
    release_id: str,
    compare_release_id: str = "",
) -> dict:
    """Build and return QA reports for a release.

    Mirrors server.py:435-443.  Appends generated_at to the result.
    """
    if not release_id:
        raise ValueError("release_id is required")
    reports = core.build_qa_reports(conn, release_id, compare_release_id or None)
    reports["generated_at"] = core.now()
    return reports


# ---------------------------------------------------------------------------
# QA analyze-log (synchronous) — POST /api/qa/analyze-log
# ---------------------------------------------------------------------------

def analyze_qa_log_sync(
    conn: sqlite3.Connection,
    release_id: str,
    db_path: Path,
) -> dict:
    """Run LLM analysis synchronously and return results.

    Mirrors server.py:973-982.  Synchronous — blocks until analysis completes.
    """
    return core.qa_analyze_log(conn, db_path, release_id)


# ---------------------------------------------------------------------------
# QA analyze-log/start (async) — POST /api/qa/analyze-log/start
# ---------------------------------------------------------------------------

def start_qa_analysis_job(
    release_id: str,
    *,
    user: str,
    role: str,
    db_path: Path,
) -> dict:
    """Start an async LLM analysis job and return the initial job snapshot.

    Mirrors server.py:984-993.  Delegates to QaJobRegistry.
    """
    from app.services.qa_jobs import get_registry

    registry = get_registry()
    return registry.start_job(release_id, user=user, role=role, db_path=db_path)


# ---------------------------------------------------------------------------
# QA analyze-log/status — GET /api/qa/analyze-log/status
# ---------------------------------------------------------------------------

def get_qa_analysis_status(
    job_id: str,
    *,
    user: str,
    role: str,
) -> dict | None:
    """Return job status snapshot or None if unknown/expired.

    Mirrors server.py:344-355.  AuthzError raised inside poll_job if caller
    isn't RM and doesn't own the job.
    """
    from app.services.qa_jobs import get_registry

    return get_registry().poll_job(job_id, user=user, role=role)
