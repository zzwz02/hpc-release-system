"""Weekly report router (AI助手 → 周报): the JIRA update check of the weekly reports.

Roles that can open the tab (RM, Owner) read the stored weeks; uploading,
analysing and the member table need the weekly-report.run capability (RM).

  POST /api/weekly-report/uploads?filename=      — raw .xlsx body, parsed in memory
  POST /api/weekly-report/uploads/{id}/analyze   — start the background analysis
  GET  /api/weekly-report/jobs/{id}              — analysis progress
  POST /api/weekly-report/jobs/{id}/cancel       — stop a running analysis
  GET  /api/weekly-report/weeks                  — analysed weeks, newest first
  GET  /api/weekly-report/weeks/{week_key}       — one week's record and summary
  GET  /api/weekly-report/weeks/{week_key}/findings
  GET  /api/weekly-report/weeks/{week_key}/export?format=md|csv
  GET/PUT /api/weekly-report/members             — sheet ↔ JIRA username table
"""
from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.errors import ApiError
from app.config import settings
from app.deps import get_db, require_capability, require_tab_access
from app.services import weekly_report_service
from app.services.weekly_report_jobs import MAX_UPLOAD_BYTES, registry

router = APIRouter(prefix="/api/weekly-report", tags=["weekly-report"])
require_view = require_tab_access("weekly-report", message="无权访问周报")
require_run = require_capability("weekly-report.run", message="只有 RM 可以上传、分析周报和管理成员表")


class AnalyzeBody(BaseModel):
    report_date: str = ""
    active_hours: int | None = None
    consistency: bool = True


class MembersBody(BaseModel):
    members: list[dict]
    version: str


async def _read_body(request: Request) -> bytes:
    """Read the upload, refusing it as soon as it passes the size limit."""
    limit_text = f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise ApiError(413, limit_text)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise ApiError(413, limit_text)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/uploads")
async def upload_report(
    request: Request,
    filename: str,
    user: dict = Depends(require_run),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    content = await _read_body(request)
    return await run_in_threadpool(
        registry.add_upload, conn,
        filename=filename, content=content, user=user["username"], role=user["role"],
    )


@router.post("/uploads/{upload_id}/analyze")
def analyze_upload(
    upload_id: str,
    body: AnalyzeBody,
    user: dict = Depends(require_run),
) -> dict:
    return registry.start_job(
        upload_id,
        report_date=body.report_date,
        active_hours=body.active_hours,
        consistency=body.consistency,
        user=user["username"],
        role=user["role"],
        db_path=settings.db_path,
    )


@router.get("/jobs/{job_id}")
def job_status(job_id: str, _user: dict = Depends(require_run)) -> dict:
    job = registry.poll_job(job_id)
    if job is None:
        raise ApiError(404, "分析任务不存在或已过期")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, _user: dict = Depends(require_run)) -> dict:
    job = registry.cancel_job(job_id)
    if job is None:
        raise ApiError(404, "分析任务不存在或已过期")
    return job


@router.get("/weeks")
def list_weeks(
    _user: dict = Depends(require_view),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"weeks": weekly_report_service.list_weeks(conn)}


def _week_or_404(conn: sqlite3.Connection, week_key: str) -> dict:
    week = weekly_report_service.get_week(conn, week_key)
    if week is None:
        raise ApiError(404, f"没有 {week_key} 的周报检查结果")
    return week


@router.get("/weeks/{week_key}")
def get_week(
    week_key: str,
    _user: dict = Depends(require_view),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"week": _week_or_404(conn, week_key)}


@router.get("/weeks/{week_key}/findings")
def list_findings(
    week_key: str,
    person: str = "",
    problem: str = "",
    _user: dict = Depends(require_view),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    _week_or_404(conn, week_key)
    findings = weekly_report_service.list_findings(conn, week_key, person=person, problem=problem)
    return {"findings": findings}


@router.get("/weeks/{week_key}/export")
def export_week(
    week_key: str,
    format: str = "md",
    _user: dict = Depends(require_view),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    if format not in ("md", "csv"):
        raise ValueError("format 只能是 md 或 csv")
    week = _week_or_404(conn, week_key)
    findings = weekly_report_service.list_findings(conn, week_key) or []
    if format == "csv":
        text, media_type = weekly_report_service.export_csv(findings), "text/csv"
    else:
        text, media_type = weekly_report_service.export_markdown(week, findings), "text/markdown"
    filename = weekly_report_service.export_filename(week, format)
    return Response(
        content=text.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/members")
def get_members(
    _user: dict = Depends(require_run),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return weekly_report_service.get_members(conn)


@router.put("/members")
def put_members(
    body: MembersBody,
    user: dict = Depends(require_run),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return weekly_report_service.save_members(
        conn, body.members, version=body.version, user=user["username"], role=user["role"],
    )
