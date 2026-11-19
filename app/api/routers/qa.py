"""QA router — App verdicts, report failures, workbooks and legacy log analysis.

Faithful port of server.py GET/POST QA handlers.

Endpoint paths (legacy release routes retain their original paths):
  GET  /api/qa/analyze-log/status
  GET  /api/qa-log/download           (note: NOT under /api/qa prefix)
  GET  /api/qa-reports                (note: NOT under /api/qa prefix)
  POST /api/qa/status-batch
  POST /api/qa/build-lock
  POST /api/qa/build-lock-batch
  POST /api/qa/upload-log            (retired, returns 410)
  GET  /api/qa/failure-summary
  GET  /api/qa/report
  GET  /api/qa/delivery-images
  POST /api/qa/analyze-log
  POST /api/qa/analyze-log/start
  GET  /api/qa/test-results
  GET  /api/qa/test-commands/export
  GET  /api/qa/test-results/export
  POST /api/qa/test-results/upload
  GET  /api/qa/test-results/{report_id}/download

The router prefix is left empty (tags=["qa"]) so we can host paths both
under /api/qa/ and at /api/qa-* to match the old server exactly.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.errors import ApiError
from app.config import settings
from app.deps import get_db, require_capability, require_tab_access
from app.services import community_artifacts_service
from app.services import qa_service, qa_results_service, qa_report_service

router = APIRouter(tags=["qa"])
require_qa_tab_access = require_tab_access("qa", message="无权访问 QA 页签")

require_qa_analysis_status = require_capability(
    "qa.edit",
    message="只有 QA 或 RM 可查看 AI 分析进度",
)
require_qa_status_edit = require_capability(
    "qa.edit",
    message="只有 QA 或 RM 可标注 QA 状态",
)
require_build_lock_edit = require_capability("qa.build_lock", message="只有 QA 或 RM 可锁定或解锁构建")

require_qa_log_upload = require_capability(
    "qa.edit",
    message="只有 QA 或 RM 可上传 QA log",
)
require_qa_results_upload = require_capability(
    "qa.edit", message="只有 QA 或 RM 可上传测试结果",
)
require_qa_analysis = require_capability(
    "qa.edit",
    message="只有 QA 或 RM 可使用 AI 分析 log",
)


# ---------------------------------------------------------------------------
# GET /api/qa/analyze-log/status
# ---------------------------------------------------------------------------

@router.get("/api/qa/analyze-log/status")
def api_qa_analyze_log_status(
    job_id: str = Query(default=""),
    _user: dict = Depends(require_qa_analysis_status),
) -> dict:
    """Poll an async LLM analysis job.

    Mirrors server.py:344-355.
    Returns job snapshot dict on success; 404-style error dict when not found.
    """
    if not job_id:
        raise ValueError("job_id is required")
    job = qa_service.get_qa_analysis_status(job_id)
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
    _user: dict = Depends(require_qa_tab_access),
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
    _user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return QA release report and test-command tables.

    Mirrors server.py:435-443.
    """
    return qa_service.get_qa_reports(conn, release_id, compare_release_id)


@router.get("/api/qa/test-commands")
def version_test_commands(
    response: Response,
    release_id: str = Query(),
    hpc_version: str = Query(default="latest"),
    _user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    from app.services import qa_commands_service

    response.headers["Cache-Control"] = "no-store"
    return qa_commands_service.query(conn, release_id=release_id, hpc_version=hpc_version)


@router.get("/api/qa/test-commands/export")
def export_version_test_commands(
    release_id: str = Query(),
    hpc_version: str = Query(default="latest"),
    template: bool = Query(default=False),
    _user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    from app.services import qa_commands_service

    content, filename = qa_commands_service.export_csv(
        conn, release_id=release_id, hpc_version=hpc_version, template=template,
    )
    return Response(content, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store",
    })


# ---------------------------------------------------------------------------
# POST /api/qa/community-artifacts
# ---------------------------------------------------------------------------

@router.post("/api/qa/community-artifacts")
def api_qa_community_artifacts(
    body: dict,
    _user: dict = Depends(require_qa_tab_access),
) -> dict:
    """Return developer-community artifact JSON filenames and contents.

    Each request shells out to hpcbox, so the QA page always sees the latest
    qpub result and never relies on a server-side cache.
    """
    return community_artifacts_service.fetch_community_artifacts(
        body.get("sdkversion", ""),
        category=body.get("category", "ai4s"),
    )


# ---------------------------------------------------------------------------
# POST /api/qa/status-batch
# ---------------------------------------------------------------------------

@router.post("/api/qa/status-batch")
def api_qa_status_batch(
    body: dict,
    user: dict = Depends(require_qa_status_edit),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Apply QA status annotations in batch.

    Mirrors server.py:941-952.
    """
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
    _user: dict = Depends(require_qa_log_upload),
) -> dict:
    """Old upload clients must use the validated QA-report workflow."""
    raise ApiError(410, "旧日志上传接口已停用，请在 QA log 上传测试结果 Excel；QA report 自动汇总失败项")


class BuildLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    release_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    locked: StrictBool
    expected_revision: int = Field(strict=True, ge=0)


@router.post("/api/qa/build-lock")
def set_build_lock(body: BuildLockRequest, user: dict = Depends(require_build_lock_edit),
                   conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return qa_service.set_build_lock(conn, body.release_id, body.app_id, locked=body.locked,
                                    expected_revision=body.expected_revision, user=user["username"], role=user["role"])


class BuildLockBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    app_id: str = Field(min_length=1)
    expected_revision: int = Field(strict=True, ge=0)


class BuildLockBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    release_id: str = Field(min_length=1)
    locked: StrictBool
    items: list[BuildLockBatchItem] = Field(min_length=1)


@router.post("/api/qa/build-lock-batch")
def set_build_locks_batch(body: BuildLockBatchRequest, user: dict = Depends(require_build_lock_edit),
                         conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return qa_service.set_build_locks_batch(conn, body.release_id, locked=body.locked,
                                           items=[item.model_dump() for item in body.items],
                                           user=user["username"], role=user["role"])


@router.get("/api/qa/report")
def qa_report(
    response: Response,
    release_id: str = Query(default=""),
    user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return qa_report_service.get_report(conn, user=user, release_id=release_id)


@router.get("/api/qa/failure-summary")
def qa_failure_summary(
    response: Response,
    release_id: str = Query(),
    _user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return qa_results_service.failure_summary(conn, release_id)


@router.get("/api/qa/delivery-images")
def qa_delivery_images(
    response: Response,
    release_id: str = Query(),
    _user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return qa_service.delivery_images_view(conn, release_id)


# ---------------------------------------------------------------------------
# GET /api/qa/analyze-log/current — the release's analysis still running
# ---------------------------------------------------------------------------

@router.get("/api/qa/analyze-log/current")
def api_qa_analyze_log_current(
    release_id: str = Query(default=""),
    _user: dict = Depends(require_qa_analysis_status),
) -> dict:
    """Let a reopened QA page pick up an analysis still running for the release."""
    if not release_id:
        raise ValueError("release_id is required")
    return {"job": qa_service.get_running_qa_analysis(release_id)}


# ---------------------------------------------------------------------------
# POST /api/qa/analyze-log/cancel — stop a running analysis, whoever started it
# ---------------------------------------------------------------------------

@router.post("/api/qa/analyze-log/cancel")
def api_qa_analyze_log_cancel(
    body: dict,
    _user: dict = Depends(require_qa_analysis),
) -> dict:
    job_id = body.get("job_id", "")
    if not job_id:
        raise ValueError("job_id is required")
    job = qa_service.cancel_qa_analysis(job_id)
    if job is None:
        raise ApiError(404, "AI 分析任务不存在或已过期")
    return job


# ---------------------------------------------------------------------------
# GET /api/qa/ai-suggestions, POST /api/qa/ai-suggestions/discard
# ---------------------------------------------------------------------------

@router.get("/api/qa/ai-suggestions")
def api_qa_ai_suggestions(
    release_id: str = Query(default=""),
    _user: dict = Depends(require_qa_analysis_status),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """The release's last AI analysis still waiting for review; null when none."""
    if not release_id:
        raise ValueError("release_id is required")
    return {"suggestions": qa_service.get_ai_suggestions(conn, release_id)}


@router.post("/api/qa/ai-suggestions/discard")
def api_qa_ai_suggestions_discard(
    body: dict,
    user: dict = Depends(require_qa_analysis),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    release_id = body.get("release_id", "")
    if not release_id:
        raise ValueError("release_id is required")
    return qa_service.discard_ai_suggestions(conn, release_id, user=user["username"], role=user["role"])


# ---------------------------------------------------------------------------
# POST /api/qa/analyze-log  (synchronous — blocks until done)
# ---------------------------------------------------------------------------

@router.post("/api/qa/analyze-log")
def api_qa_analyze_log(
    body: dict,
    user: dict = Depends(require_qa_analysis),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Run LLM analysis on the uploaded QA log synchronously.

    Mirrors server.py:973-982.
    """
    return qa_service.analyze_qa_log_sync(conn, body["release_id"], user=user["username"])


# ---------------------------------------------------------------------------
# POST /api/qa/analyze-log/start  (async — returns job snapshot immediately)
# ---------------------------------------------------------------------------

@router.post("/api/qa/analyze-log/start")
def api_qa_analyze_log_start(
    body: dict,
    user: dict = Depends(require_qa_analysis),
) -> dict:
    """Start an async LLM analysis job and return the initial job state.

    Mirrors server.py:984-993.
    Note: no DB connection injected — the background thread opens its own.
    """
    release_id = body.get("release_id", "")
    if not release_id:
        raise ValueError("release_id is required")
    return qa_service.start_qa_analysis_job(
        release_id,
        user=user["username"],
        role=user["role"],
        db_path=settings.db_path,
    )


@router.post("/api/qa/test-results/upload")
def upload_test_results(
    body: dict,
    user: dict = Depends(require_qa_results_upload),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return qa_results_service.upload(
        conn,
        filename=body.get("filename", ""),
        content_b64=body.get("content_base64", ""),
        user=user["username"],
        role=user["role"],
        release_id=body.get('release_id', ''),
        revision=body.get('revision'),
    )


def result_filters(
    release_id: str = Query(),
    search: str = Query(default=""),
    table_filter: str = Query(default="", alias="filter"),
    column_filters: str = Query(default="{}"),
    sort_by: str = Query(default=""),
    sort_direction: Literal["asc", "desc"] = Query(default="asc"),
) -> dict:
    try:
        columns = json.loads(column_filters)
    except ValueError as exc:
        raise ApiError(400, "列筛选须为 JSON 对象") from exc
    if not isinstance(columns, dict):
        raise ApiError(400, "列筛选须为 JSON 对象")
    return {'release_id': release_id, "search": search, "table_filter": table_filter, "column_filters": columns,
            "sort_by": sort_by, "sort_direction": sort_direction}


@router.get("/api/qa/test-results")
def test_results(
    response: Response,
    filters: dict = Depends(result_filters),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    all_rows: bool = Query(default=False),
    user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return qa_results_service.query(conn, **filters, offset=0 if all_rows else offset,
                                    limit=None if all_rows else limit)


@router.get("/api/qa/test-results/export")
def export_test_results(
    filters: dict = Depends(result_filters),
    user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    return Response(
        qa_results_service.export_csv(conn, **filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="qa_test_results.csv"',
                 "Cache-Control": "no-store"},
    )


@router.get("/api/qa/test-results/{report_id}/download")
def download_test_results(
    report_id: int,
    user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    content, filename = qa_results_service.download(conn, report_id)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/qa/test-results/{report_id}/environment")
def preview_test_environment(
    report_id: int,
    response: Response,
    name: str = Query(min_length=1),
    user: dict = Depends(require_qa_tab_access),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return qa_results_service.environment_preview(conn, report_id, name)
