"""CICD router — task and request management.

New endpoints (R3 §3.2):
  POST /api/cicd/apps/new     — CICD-first create app
  POST /api/cicd/tasks/abandon — RM abandons a Stopped task

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/cicd", tags=["cicd"])


# TODO Phase 2 — GET  /api/cicd/tasks           (response adds app_id field)
# TODO Phase 2 — POST /api/cicd/tasks/create
# TODO Phase 2 — POST /api/cicd/tasks/modify
# TODO Phase 2 — POST /api/cicd/tasks/delete
# TODO Phase 2 — POST /api/cicd/tasks/transfer-owner
# TODO Phase 2 — POST /api/cicd/tasks/abandon   (NEW — Ruling-A)
# TODO Phase 2 — POST /api/cicd/apps/new        (NEW — R3 CICD-first)
# TODO Phase 2 — GET  /api/cicd/requests
# TODO Phase 2 — POST /api/cicd/requests/approve
# TODO Phase 2 — POST /api/cicd/requests/return
# TODO Phase 2 — POST /api/cicd/requests/dispatch-spd
# TODO Phase 2 — POST /api/cicd/requests/deliver
