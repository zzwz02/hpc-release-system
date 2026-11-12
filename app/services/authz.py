"""Authorization helpers that need DB data.

Used by service methods (not as FastAPI Depends — they need conn + context).
"""
from __future__ import annotations

import sqlite3

from app.api.errors import AuthzError
from app.domain.permissions import has_capability
from app.repositories import apps_repo


def require_app_audit_access(
    conn: sqlite3.Connection,
    app_id: str,
    username: str,
    role: str,
) -> None:
    """RM / Admin / QA see every App's history; Owners see their own Apps."""
    if has_capability(role, "app.audit.view.any"):
        return
    if has_capability(role, "app.audit.view.owned"):
        app = apps_repo.get_app(conn, app_id)
        if app and username in (app.get("owners") or []):
            return
    raise AuthzError("App audit access denied")
