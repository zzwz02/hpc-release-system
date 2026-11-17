"""Background automatic reports. Completed reports persist; jobs are process-local."""
from __future__ import annotations

import secrets
import threading
import time

from app.api.errors import ApiError
from app.db.connection import connect, transaction
from app.domain import auto_weekly_report as rules
from app.integrations import jira, llm
from app.repositories import auto_weekly_report_repo as repo
from app.repositories.audit_repo import log_audit
from app.services import auto_weekly_report_service as service
from app.timeutil import beijing_timestamp, elapsed_seconds

JOB_TTL_SECONDS = 3600
# A report can make many LLM/attachment requests. Queueing is not implicit.
MAX_RUNNING_JOBS = 2


class Registry:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}

    def start(self, opts: dict, user: dict, db_path) -> dict:
        jira._require_config()
        llm.require_config()
        with self.lock:
            self.cleanup()
            running = [j for j in self.jobs.values() if j["status"] == "running"]
            if any(j["options"]["report_date"] == opts["report_date"] for j in running):
                raise ApiError(409, "本周的自动周报正在生成，请等待或先中止")
            if len(running) >= MAX_RUNNING_JOBS:
                raise ApiError(409, "自动周报生成任务已满，请稍后重试")
            job = {"id": secrets.token_urlsafe(12), "status": "running", "message": "任务已提交", "error": "",
                   "options": opts, "user": user["username"], "started_at": time.time(), "finished_at": 0,
                   "report_id": "", "cancel": llm.CancelToken(), "meter": llm.LlmMeter()}
            self.jobs[job["id"]] = job
            view = self.view(job)
        threading.Thread(target=self.run, args=(job, user, db_path), daemon=True).start()
        return view

    def view(self, job):
        return {**{k: job[k] for k in ("id", "status", "message", "error", "options", "user", "report_id")},
                "stopping": job["cancel"].cancelled and job["status"] == "running",
                "elapsed_seconds": elapsed_seconds(job["started_at"], job["finished_at"]), "llm": job["meter"].snapshot()}

    def cleanup(self):
        for key, job in list(self.jobs.items()):
            if job["status"] != "running" and time.time() - job["finished_at"] > JOB_TTL_SECONDS:
                del self.jobs[key]

    def get(self, job_id):
        with self.lock:
            self.cleanup()
            if job_id not in self.jobs:
                raise ApiError(404, "生成任务不存在或已过期")
            return self.view(self.jobs[job_id])

    def running(self):
        with self.lock:
            return [self.view(j) for j in self.jobs.values() if j["status"] == "running"]

    def cancel(self, job_id):
        with self.lock:
            self.get(job_id)
            job = self.jobs[job_id]
            if job["status"] != "running":
                raise ApiError(409, "生成任务已结束")
            job["cancel"].cancel()
            job["message"] = "正在中止，等待当前 JIRA 读取结束"
            return self.view(job)

    def run(self, job, user, db_path):
        conn = None
        def progress(message):
            with self.lock:
                if not job["cancel"].cancelled:
                    job["message"] = message
        try:
            conn = connect(db_path)
            result = service.generate(conn, job["options"], cancel=job["cancel"], meter=job["meter"], progress=progress)
            with self.lock:
                if job["cancel"].cancelled:
                    raise llm.LLMCancelled("自动周报已中止")
                report = {**job["options"], **result, "id": secrets.token_urlsafe(12), "created_by": user["username"], "created_at": beijing_timestamp()}
                with transaction(conn, immediate=True):
                    report["id"] = repo.replace(conn, report)
                    log_audit(conn, f"生成自动周报 {report['report_date']}", ts=report["created_at"], user=user["username"], role=user["role"],
                              event="auto_weekly_report_generate", detail={"id": report["id"], **job["options"]})
                job.update(status="completed", report_id=report["id"], message="自动周报已生成", finished_at=time.time())
        except llm.LLMCancelled:
            with self.lock:
                job.update(status="cancelled", message="自动周报已中止，已有周报保留", finished_at=time.time())
        except Exception as exc:
            with self.lock:
                if job["cancel"].cancelled:
                    job.update(status="cancelled", message="自动周报已中止，已有周报保留", finished_at=time.time())
                else:
                    # Known integration errors are readable; do not expose raw URLs or credentials.
                    message = rules.mask(str(exc)) if isinstance(exc, (ValueError, RuntimeError)) else f"生成失败（{type(exc).__name__}），请重试或检查 JIRA 连接"
                    job.update(status="failed", error=message, message=message, finished_at=time.time())
        finally:
            if conn is not None:
                conn.close()


registry = Registry()
