"""QA LLM analysis job registry — in-process job tracking.

The QaJobRegistry is attached to app.state in lifespan.
Background threads open their own DB connections (per plan §3.6 — do NOT
reuse the per-request connection; this mirrors server.py:229 behaviour).

# TODO Phase 2 — implement
"""
from __future__ import annotations

import threading
import time


class QaJobRegistry:
    """Thread-safe registry for async QA analysis jobs.

    # TODO Phase 2 — implement start_job, poll_job, cleanup
    """

    _TTL_SECONDS = 3600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start_job(self, job_id: str, release_id: str, *, db_path: str) -> None:
        """Launch a background LLM analysis thread and register the job.

        # TODO Phase 2
        """
        raise NotImplementedError

    def poll_job(self, job_id: str) -> dict | None:
        """Return a snapshot of the job state, or None if unknown.

        # TODO Phase 2
        """
        raise NotImplementedError

    def cleanup_stale(self) -> None:
        """Remove completed/failed jobs older than TTL.

        # TODO Phase 2
        """
        cutoff = time.time() - self._TTL_SECONDS
        with self._lock:
            stale = [
                jid
                for jid, job in self._jobs.items()
                if job.get("status") != "running" and float(job.get("updated_at") or 0) < cutoff
            ]
            for jid in stale:
                self._jobs.pop(jid, None)
