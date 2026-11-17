"""AI助手 → 周报 → 自动周报. Same view/run permissions as the Excel check."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.routers.weekly_report import require_run, require_view
from app.config import settings
from app.db.connection import transaction
from app.deps import get_db
from app.domain import auto_weekly_report as rules
from app.integrations import jira
from app.repositories import auto_weekly_report_repo as repo
from app.repositories.audit_repo import log_audit
from app.services.auto_weekly_report_jobs import registry
from app.timeutil import beijing_timestamp

router = APIRouter(prefix="/api/weekly-report/auto", tags=["weekly-report"])


class GenerateBody(BaseModel):
    group: str = Field(max_length=100)
    since: str = Field(max_length=30)
    until: str = Field(max_length=30)
    report_date: str = Field(max_length=10)


class EditBody(BaseModel):
    markdown: str = Field(min_length=1, max_length=200000)
    notes: str = Field(max_length=200000)
    revision: int = Field(ge=1)


def report_or_404(conn, report_id):
    report = repo.get(conn, report_id)
    if report is None:
        raise ApiError(404, "自动周报不存在")
    report["jira_browse_url"] = jira.browse_url()
    return report


@router.get("/defaults")
def defaults(_user: dict = Depends(require_view)):
    return rules.defaults()


@router.post("/generate")
def generate(body: GenerateBody, user: dict = Depends(require_run)):
    return registry.start(rules.options(**body.model_dump()), user, settings.db_path)


@router.get("/jobs")
def running(_user: dict = Depends(require_run)):
    return {"jobs": registry.running()}


@router.get("/jobs/{job_id}")
def status(job_id: str, _user: dict = Depends(require_run)):
    return registry.get(job_id)


@router.post("/jobs/{job_id}/watch")
async def watch(job_id: str, request: Request, _user: dict = Depends(require_run)):
    registry.get(job_id)
    async def events():
        while not await request.is_disconnected():
            job = registry.get(job_id)
            yield json.dumps(job, ensure_ascii=False) + "\n"
            if job["status"] != "running":
                return
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, _user: dict = Depends(require_run)):
    return registry.cancel(job_id)


@router.get("/reports")
def list_reports(_user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)):
    return {"reports": repo.list_reports(conn)}


@router.get("/reports/{report_id}")
def detail(report_id: str, _user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)):
    return report_or_404(conn, report_id)


@router.put("/reports/{report_id}")
def edit(report_id: str, body: EditBody, user: dict = Depends(require_run), conn: sqlite3.Connection = Depends(get_db)):
    if not body.markdown.strip():
        raise ValueError("周报正文不能为空")
    with transaction(conn, immediate=True):
        report_or_404(conn, report_id)
        if not repo.update(conn, report_id, rules.mask(body.markdown), rules.mask(body.notes), body.revision):
            raise ApiError(409, "周报已被其他人修改，请保留草稿并刷新后重试")
        log_audit(conn, "编辑自动周报", ts=beijing_timestamp(), user=user["username"], role=user["role"],
                  event="auto_weekly_report_edit", detail={"id": report_id, "revision": body.revision + 1})
    return report_or_404(conn, report_id)


@router.get("/reports/{report_id}/export")
def export(report_id: str, _user: dict = Depends(require_view), conn: sqlite3.Connection = Depends(get_db)):
    report = report_or_404(conn, report_id)
    content = report["markdown"] + "\n---\n\n# 交付说明\n\n" + report["notes"]
    filename = f"周报_{report['report_date']}_简洁版.md"
    return Response(content.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
