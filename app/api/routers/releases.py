"""Releases router — release lifecycle CRUD.

New endpoints (R2 §3.2):
  GET /api/releases           — list (per-section refresh)
  GET /api/releases/{id}      — single release detail

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/releases", tags=["releases"])


# TODO Phase 2 — GET  /api/releases
# TODO Phase 2 — GET  /api/releases/{release_id}
# TODO Phase 2 — POST /api/releases/new
# TODO Phase 2 — POST /api/releases/clone
# TODO Phase 2 — POST /api/releases/lock
# TODO Phase 2 — POST /api/releases/update-deadlines
# TODO Phase 2 — POST /api/releases/delete
