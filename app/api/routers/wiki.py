"""Wiki router — internal dev wiki CRUD.

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# TODO Phase 2 — GET  /api/wiki/articles
# TODO Phase 2 — GET  /api/wiki/articles/{id}
# TODO Phase 2 — POST /api/wiki/articles/new
# TODO Phase 2 — POST /api/wiki/articles/update
# TODO Phase 2 — POST /api/wiki/articles/delete
# TODO Phase 2 — POST /api/wiki/images/upload
# TODO Phase 2 — GET  /api/wiki/images/{id}
