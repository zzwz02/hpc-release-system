"""QA LLM analysis job registry — in-process job tracking.

Thread-safe job registry for async QA analysis jobs, mirroring the
module-level _QA_ANALYSIS_JOBS / _QA_ANALYSIS_LOCK pattern in server.py:38-40.

Usage:
    The QaJobRegistry singleton is created at module load.  main.py lifespan
    can attach it to app.state (optional — routers import it directly via
    get_registry()).  Background threads open their OWN DB connection (never
    reuse the per-request connection), mirroring server.py:229.
"""
from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

from app.db.connection import connect

# ---------------------------------------------------------------------------
# Module-level singleton (mirrors server.py:38-40)
# ---------------------------------------------------------------------------

_registry: QaJobRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> QaJobRegistry:
    """Return the module-level QaJobRegistry, creating it on first call."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = QaJobRegistry()
    return _registry


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------

class QaJobRegistry:
    """Thread-safe registry for async QA analysis jobs.

    Mirrors server.py:38-295 (module-level _QA_ANALYSIS_JOBS + helpers).
    """

    _TTL_SECONDS = 3600  # mirrors server.py:40

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_job(
        self,
        release_id: str,
        *,
        user: str,
        role: str,
        db_path: Path,
    ) -> dict:
        """Launch a background LLM analysis thread and return initial snapshot.

        Mirrors server.py:start_qa_analysis_job (260-283).
        """
        self.cleanup_stale()
        job_id = secrets.token_urlsafe(12)
        now = self._now()
        job: dict = {
            "job_id": job_id,
            "release_id": release_id,
            "user": user,
            "role": role,
            "status": "running",
            "stage": "queued",
            "message": "AI 分析任务已提交",
            "started_at": now,
            "updated_at": now,
            "finished_at": 0,
            "token_count": 0,
            "result": None,
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, release_id, db_path),
            daemon=True,
        )
        thread.start()
        return self._snapshot(job)

    def poll_job(self, job_id: str, *, user: str, role: str) -> dict | None:
        """Return a job snapshot or None if unknown/expired.

        Mirrors server.py:get_qa_analysis_job (286-294).
        Raises AuthzError if caller isn't RM and doesn't own the job.
        """
        from app.api.errors import AuthzError

        self.cleanup_stale()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if role != "RM" and job.get("user") != user:
                raise AuthzError("无权查看该 AI 分析任务")
            return self._snapshot(job)

    def cleanup_stale(self) -> None:
        """Remove completed/failed jobs older than TTL.

        Mirrors server.py:_cleanup_qa_analysis_jobs (186-195).
        """
        cutoff = self._now() - self._TTL_SECONDS
        with self._lock:
            stale = [
                jid
                for jid, job in self._jobs.items()
                if job.get("status") != "running"
                and float(job.get("updated_at") or 0) < cutoff
            ]
            for jid in stale:
                self._jobs.pop(jid, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> float:
        """Mirrors server.py:_qa_analysis_now."""
        return time.time()

    def _update(self, job_id: str, **updates: object) -> None:
        """Update job fields atomically; always bump updated_at.

        Mirrors server.py:_update_qa_analysis_job (218-223).
        """
        updates["updated_at"] = self._now()
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(updates)

    @staticmethod
    def _snapshot(job: dict) -> dict:
        """Build a safe public snapshot of a job dict.

        Mirrors server.py:_qa_job_snapshot (198-215).
        """
        payload: dict = {
            "job_id": job["job_id"],
            "release_id": job["release_id"],
            "status": job["status"],
            "stage": job.get("stage", ""),
            "message": job.get("message", ""),
            "started_at": job.get("started_at", 0),
            "updated_at": job.get("updated_at", 0),
            "finished_at": job.get("finished_at", 0),
        }
        if job.get("error"):
            payload["error"] = job["error"]
        if job.get("token_count") is not None:
            payload["token_count"] = job.get("token_count", 0)
        if job.get("result") is not None:
            payload["result"] = job["result"]
        return payload

    def _run_job(self, job_id: str, release_id: str, db_path: Path) -> None:
        """Background thread: open own conn, run LLM analysis, update registry.

        Mirrors server.py:_run_qa_analysis_job (226-257).
        Background thread MUST open its own connection (brief §3.6 / server.py:229).
        """
        conn = None
        try:
            conn = connect(db_path)

            def progress(stage: str, message: str, **extra: object) -> None:
                self._update(job_id, stage=stage, message=message, **extra)

            from app.services.qa_analysis_service import analyze_qa_log

            result = analyze_qa_log(conn, release_id, progress=progress)
            self._update(
                job_id,
                status="completed",
                stage="completed",
                message="AI 分析完成",
                result=result,
                finished_at=self._now(),
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message=f"AI 分析失败：{exc}",
                error=str(exc),
                finished_at=self._now(),
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
