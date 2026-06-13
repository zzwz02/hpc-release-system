"""Artifacts router — release artifact generation and download.

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


# TODO Phase 2 — POST /api/artifacts/generate
# TODO Phase 2 — GET  /api/artifacts/{release_id}/{kind}
# TODO Phase 2 — POST /api/artifacts/finalize
