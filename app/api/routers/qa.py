"""QA router — QA status, log upload, LLM analysis.

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/qa", tags=["qa"])


# TODO Phase 2 — POST /api/qa/set-status
# TODO Phase 2 — POST /api/qa/upload-log
# TODO Phase 2 — POST /api/qa/analyze      (starts async LLM job)
# TODO Phase 2 — GET  /api/qa/analyze/{job_id}  (poll job status)
