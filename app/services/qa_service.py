"""QA service — status batch updates, log upload/download, and LLM analysis.

Faithful 1:1 port of server.py QA handlers + core.py QA functions.
All orchestration delegates to release_system.core; this layer just wires
auth context (user/role/db_path) to the right core call.
"""
from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import release_system.core as core

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

    Mirrors server.py:941-952.  Returns {"ok": True, "updated": n}.
    """
    updated = core.qa_set_status_batch(conn, release_id, items, user=user, role=role)
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
