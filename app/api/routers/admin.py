"""Admin router — user/role management and system maintenance.

Endpoints (faithful port of server.py):
  GET  /api/admin/users                — list all users (Admin only)
  POST /api/admin/users/set-role       — change a user's role (Admin only)
  POST /api/admin/clear-db             — clear business data (Admin + password)
  POST /api/admin/apps/delete          — delete an app globally (Admin only)

Ruling-C (Admin out of release business) is Phase 4 — this ports existing
behavior verbatim.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from app.deps import get_db, require_roles
from app.services import admin_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------

def get_users(
    _user: dict = Depends(require_roles("Admin", message="Admin role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return all users.

    Mirrors server.py:336-338.
    """
    return {"users": admin_service.list_users(conn)}


router.add_api_route("/users", get_users, methods=["GET"])


# ---------------------------------------------------------------------------
# POST /api/admin/users/set-role
# ---------------------------------------------------------------------------

async def post_set_role(
    request: Request,
    user: dict = Depends(require_roles("Admin", message="Admin role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Update a user's role.

    Mirrors server.py:582-593.
    Raises ValueError (→ 400) if username or role missing, or user not found.
    """
    body = await request.json()
    if not body.get("username") or not body.get("role"):
        raise ValueError("username 和 role 均为必填")
    admin_service.update_user_role(
        conn,
        body["username"],
        role=body["role"],
        actor=user["username"],
    )
    return {"ok": True}


router.add_api_route("/users/set-role", post_set_role, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/admin/clear-db
# ---------------------------------------------------------------------------

async def post_clear_db(
    request: Request,
    user: dict = Depends(require_roles("Admin", message="Admin role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Clear all business data after password re-verification.

    Mirrors server.py:557-580.
    The connection is released inside the service before the backup copy is
    made, then a fresh connection is opened for the clear.
    """
    body = await request.json()
    if body.get("confirm") not in {"清空数据库", "CLEAR_DATABASE"}:
        raise RuntimeError("确认文本必须是：清空数据库 或 CLEAR_DATABASE")
    backup_name = admin_service.clear_business_data(
        conn,
        password=str(body.get("password") or ""),
        actor=user["username"],
    )
    return {"ok": True, "backup": backup_name}


router.add_api_route("/clear-db", post_clear_db, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/admin/apps/delete
# ---------------------------------------------------------------------------

async def post_delete_app(
    request: Request,
    user: dict = Depends(require_roles("Admin", message="Admin role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Delete an app globally after confirmation.

    Mirrors server.py:749-757.
    Raises RuntimeError (→ 400) if confirm != app_id.
    """
    body = await request.json()
    result = admin_service.delete_app(
        conn,
        body.get("app_id", ""),
        confirm=body.get("confirm", ""),
        actor=user["username"],
    )
    return result


router.add_api_route("/apps/delete", post_delete_app, methods=["POST"])
