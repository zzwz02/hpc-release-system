"""Admin router — user/role management and system maintenance (Ruling-C).

Admin is confined to:
  - User and role management
  - DB backup / clear
  - Global app delete
  - App audit read-only

No release business endpoints here.

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin", tags=["admin"])


# TODO Phase 2 — GET  /api/admin/users
# TODO Phase 2 — POST /api/admin/users/create
# TODO Phase 2 — POST /api/admin/users/update-role
# TODO Phase 2 — POST /api/admin/users/delete
# TODO Phase 2 — POST /api/admin/db/backup
# TODO Phase 2 — POST /api/admin/db/clear
# TODO Phase 2 — POST /api/admin/apps/delete    (global, system-maintenance)
