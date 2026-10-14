"""Weekly report check — uploads held in memory and background analysis jobs.

The uploaded workbook is never written to disk: it is parsed on arrival and
only the parsed sheets stay in this process until the analysis finishes or the
upload expires.  Jobs follow app/services/qa_jobs.py: an in-process registry,
one thread per job with its own DB connection (keep uvicorn at one worker).
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.db.connection import connect
from app.domain import weekly_report as wr
from app.repositories.audit_repo import log_audit
from app.services import weekly_check_service
from app.timeutil import beijing_timestamp

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# An upload nobody analyses is dropped after this long; so is a finished job.
UPLOAD_TTL_SECONDS = 3600
JOB_TTL_SECONDS = 3600


class WeeklyCheckRegistry:
    """Thread-safe store of pending uploads and analysis jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uploads: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------

    def add_upload(
        self,
        conn: sqlite3.Connection,
        *,
        filename: str,
        content: bytes,
        user: str,
        role: str,
    ) -> dict[str, Any]:
        """Parse an uploaded workbook, keep the parsed sheets, return its preview."""
        name = Path(filename.replace("\\", "/")).name.strip()
        if not name.lower().endswith(".xlsx"):
            raise ValueError("只接受 .xlsx 文件")
        if not content:
            raise ValueError("上传的文件是空的")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        reports, undated = wr.read_reports(content)
        preview = weekly_check_service.preview(conn, reports, undated)
        if not preview["people_count"]:
            raise ValueError("周报里没有需要检查的人员页")
        upload = {
            "upload_id": secrets.token_urlsafe(12),
            "filename": name,
            "size": len(content),
            "uploaded_by": user,
            "uploaded_at": beijing_timestamp(),
            "reports": reports,
            "undated": tuple(undated),
            "created": time.time(),
            "job_id": "",
        }
        self._cleanup()
        with self._lock:
            self._uploads[upload["upload_id"]] = upload
        log_audit(
            conn, f"上传周报 {name}（{len(content)} 字节）",
            ts=upload["uploaded_at"], user=user, role=role, event="weekly_check_upload",
            detail={"filename": name, "size": len(content), "report_date": preview["report_date"]},
        )
        conn.commit()
        return {**self._upload_view(upload), **preview}

    @staticmethod
    def _upload_view(upload: dict[str, Any]) -> dict[str, Any]:
        return {key: upload[key] for key in ("upload_id", "filename", "size", "uploaded_by", "uploaded_at")}

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def start_job(
        self,
        upload_id: str,
        *,
        report_date: str,
        active_hours: object,
        consistency: bool,
        user: str,
        role: str,
        db_path: Path,
    ) -> dict[str, Any]:
        """Validate the options, then analyse the upload in a background thread."""
        options = {
            "report_date": wr.parse_report_date(report_date) if report_date else None,
            "active_hours": (
                weekly_check_service.validate_active_hours(active_hours)
                if active_hours not in (None, "") else None
            ),
            "consistency": consistency,
        }
        self._cleanup()
        now = time.time()
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None:
                raise ValueError("上传已失效（超过 1 小时未分析，或已分析完成），请重新上传")
            if upload["job_id"] and self._jobs.get(upload["job_id"], {}).get("status") == "running":
                raise ValueError("这份周报正在分析中")
            job = {
                "job_id": secrets.token_urlsafe(12),
                "stop": threading.Event(),
                "upload_id": upload_id,
                "user": user,
                "status": "running",
                "message": "分析任务已提交",
                "done": 0,
                "total": 0,
                "week_key": "",
                "error": "",
                "started_at": now,
                "updated_at": now,
                "finished_at": 0,
            }
            self._jobs[job["job_id"]] = job
            upload["job_id"] = job["job_id"]
        threading.Thread(
            target=self._run_job,
            args=(job["job_id"], upload, options, user, role, db_path),
            daemon=True,
        ).start()
        return self._job_view(job)

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        """Ask a running analysis to stop; it ends once the calls under way return."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] != "running":
                raise ValueError("分析已经结束，无需中止")
            job["stop"].set()
            job.update(message="正在中止：等进行中的 LLM / JIRA 请求返回", updated_at=time.time())
            return self._job_view(job)

    def poll_job(self, job_id: str) -> dict[str, Any] | None:
        self._cleanup()
        with self._lock:
            job = self._jobs.get(job_id)
            return self._job_view(job) if job else None

    @staticmethod
    def _job_view(job: dict[str, Any]) -> dict[str, Any]:
        view = {
            key: job[key]
            for key in ("job_id", "status", "message", "done", "total", "week_key", "error", "started_at", "finished_at")
        }
        view["stopping"] = job["stop"].is_set() and job["status"] == "running"
        return view

    def _update(self, job_id: str, **updates: object) -> None:
        updates["updated_at"] = time.time()
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(updates)

    def _run_job(
        self,
        job_id: str,
        upload: dict[str, Any],
        options: dict[str, Any],
        user: str,
        role: str,
        db_path: Path,
    ) -> None:
        conn = None
        try:
            conn = connect(db_path)

            with self._lock:
                stop = self._jobs[job_id]["stop"]

            def progress(done: int, total: int, message: str) -> None:
                if not stop.is_set():
                    self._update(job_id, done=done, total=total, message=message)

            result = weekly_check_service.run_check(
                conn, upload["reports"], progress=progress, undated=upload["undated"],
                should_stop=stop.is_set, **options,
            )
            week = weekly_check_service.store_week(
                conn, result,
                xlsx_name=upload["filename"], xlsx_size=upload["size"],
                uploaded_by=upload["uploaded_by"], uploaded_at=upload["uploaded_at"],
                analyzed_by=user, role=role,
            )
            # The parsed workbook is no longer needed once its week is stored.
            with self._lock:
                self._uploads.pop(upload["upload_id"], None)
            self._update(
                job_id, status="completed", message="分析完成",
                week_key=week["week_key"], finished_at=time.time(),
            )
        except weekly_check_service.CheckCancelled:
            # As after a failure: the previous version stays, the upload is kept.
            self._update(
                job_id, status="cancelled", message="分析已中止，已有结果没有改动",
                finished_at=time.time(),
            )
        except Exception as exc:
            # The week's previous version, if any, is untouched; the upload
            # stays until it expires so the analysis can be started again.
            self._update(
                job_id, status="failed", message=f"分析失败：{exc}",
                error=str(exc), finished_at=time.time(),
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        now = time.time()
        with self._lock:
            running_uploads = {
                job["upload_id"] for job in self._jobs.values() if job["status"] == "running"
            }
            for upload_id, upload in list(self._uploads.items()):
                if upload_id not in running_uploads and now - upload["created"] > UPLOAD_TTL_SECONDS:
                    del self._uploads[upload_id]
            for job_id, job in list(self._jobs.items()):
                if job["status"] != "running" and now - job["updated_at"] > JOB_TTL_SECONDS:
                    del self._jobs[job_id]


registry = WeeklyCheckRegistry()
