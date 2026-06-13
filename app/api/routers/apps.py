"""Apps router — app info and snapshot management.

# TODO Phase 2 — implement all endpoints
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/apps", tags=["apps"])


# TODO Phase 2 — POST /api/apps/new
# TODO Phase 2 — POST /api/apps/update        (response gains cicd_sync field, §3.2)
# TODO Phase 2 — POST /api/apps/delete
# TODO Phase 2 — POST /api/apps/transfer-owner
# TODO Phase 2 — POST /api/apps/apply-info    (gerrit fetch + apply_app_info)
# TODO Phase 2 — POST /api/apps/fetch-all-info
