"""QA router — QA status, log upload/download, LLM analysis.

Faithful port of server.py GET/POST QA handlers.

Endpoint paths (exact from server.py):
  GET  /api/qa/analyze-log/status
  GET  /api/qa-log/download           (note: NOT under /api/qa prefix)
  GET  /api/qa-reports                (note: NOT under /api/qa prefix)
  POST /api/qa/status-batch
  POST /api/qa/upload-log
  POST /api/qa/analyze-log
  POST /api/qa/analyze-log/start

The router prefix is left empty (tags=["qa"]) so we can host paths both
under /api/qa/ and at /api/qa-* to match the old server exactly.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.api.errors import AuthzError
from app.config import settings
from app.deps import get_db, require_login
from app.services import qa_service

router = APIRouter(tags=["qa"])

# Role set used by multiple endpoints — mirrors server.py:{"QA", "RM"}
_QA_RM = {"QA", "RM"}


# ---------------------------------------------------------------------------
# GET /api/qa/analyze-log/status
# ---------------------------------------------------------------------------

@router.get("/api/qa/analyze-log/status")
def api_qa_analyze_log_status(
    job_id: str = Query(default=""),
    user: dict = Depends(require_login),
) -> dict:
    """Poll an async LLM analysis job.

    Mirrors server.py:344-355.
    Returns job snapshot dict on success; 404-style error dict when not found.
    """
    if user["role"] not in _QA_RM:
        raise AuthzError("只有 QA 或 RM 可查看 AI 分析进度")
    if not job_id:
        raise ValueError("job_id is required")
    job = qa_service.get_qa_analysis_status(
        job_id,
        user=user["username"],
        role=user["role"],
    )
    if not job:
        # Old server: self.send_json({"error": "..."}, status=404)
        return Response(
            content='{"error": "AI 分析任务不存在或已过期"}',
            status_code=404,
            media_type="application/json",
        )  # type: ignore[return-value]
    return job


# ---------------------------------------------------------------------------
# GET /api/qa-log/download
# ---------------------------------------------------------------------------

@router.get("/api/qa-log/download")
def api_qa_log_download(
    release_id: str = Query(default=""),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Stream a QA log file for download.

    Mirrors server.py:416-433.
    Returns binary octet-stream with Content-Disposition attachment header.
    """
    if not release_id:
        raise ValueError("release_id is required")
    try:
        content, filename = qa_service.get_qa_log_download(conn, release_id)
    except RuntimeError as exc:
        # Old server uses send_error(404, ...) for missing log/file.
        return Response(content=str(exc), status_code=404)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /api/qa-reports
# ---------------------------------------------------------------------------

@router.get("/api/qa-reports")
def api_qa_reports(
    release_id: str = Query(default=""),
    compare_release_id: str = Query(default=""),
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return QA release report and test-command tables.

    Mirrors server.py:435-443.
    """
    return qa_service.get_qa_reports(conn, release_id, compare_release_id)


# ---------------------------------------------------------------------------
# POST /api/qa/status-batch
# ---------------------------------------------------------------------------

@router.post("/api/qa/status-batch")
def api_qa_status_batch(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Apply QA status annotations in batch.

    Mirrors server.py:941-952.
    """
    if user["role"] not in _QA_RM:
        raise AuthzError("只有 QA 或 RM 可标注 QA 状态")
    return qa_service.set_qa_status_batch(
        conn,
        body["release_id"],
        body.get("items") or [],
        user=user["username"],
        role=user["role"],
    )


# ---------------------------------------------------------------------------
# POST /api/qa/upload-log
# ---------------------------------------------------------------------------

@router.post("/api/qa/upload-log")
def api_qa_upload_log(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Accept a base64-encoded QA log file and persist it.

    Mirrors server.py:954-971.
    """
    if user["role"] not in _QA_RM:
        raise AuthzError("只有 QA 或 RM 可上传 QA log")
    return qa_service.upload_qa_log(
        conn,
        body["release_id"],
        content_b64=body.get("content_base64", ""),
        filename=body.get("filename", "qa_log"),
        user=user["username"],
        role=user["role"],
    )


# ---------------------------------------------------------------------------
# POST /api/qa/analyze-log  (synchronous — blocks until done)
# ---------------------------------------------------------------------------

@router.post("/api/qa/analyze-log")
def api_qa_analyze_log(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Run LLM analysis on the uploaded QA log synchronously.

    Mirrors server.py:973-982.
    """
    if user["role"] not in _QA_RM:
        raise AuthzError("只有 QA 或 RM 可使用 AI 分析 log")
    return qa_service.analyze_qa_log_sync(conn, body["release_id"])


# ---------------------------------------------------------------------------
# POST /api/qa/analyze-log/start  (async — returns job snapshot immediately)
# ---------------------------------------------------------------------------

@router.post("/api/qa/analyze-log/start")
def api_qa_analyze_log_start(
    body: dict,
    user: dict = Depends(require_login),
) -> dict:
    """Start an async LLM analysis job and return the initial job state.

    Mirrors server.py:984-993.
    Note: no DB connection injected — the background thread opens its own.
    """
    if user["role"] not in _QA_RM:
        raise AuthzError("只有 QA 或 RM 可使用 AI 分析 log")
    release_id = body.get("release_id", "")
    if not release_id:
        raise ValueError("release_id is required")
    return qa_service.start_qa_analysis_job(
        release_id,
        user=user["username"],
        role=user["role"],
        db_path=settings.db_path,
    )
