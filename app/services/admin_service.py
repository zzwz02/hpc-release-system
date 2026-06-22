"""Admin service — user/role management and system maintenance.

Faithful port of admin-related handlers in server.py and
release_system/core.py.  Ruling-C (Admin out of release business) is Phase 4;
this service ports existing behavior verbatim.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.config import settings
from release_system import core as _core


def _backup_database() -> Path:
    """Create a timestamped SQLite backup file beside the main DB.

    Mirrors server.py:backup_database (server.py:1416-1421).
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = settings.db_path.parent / f"release_system_admin_backup_{stamp}.sqlite"
    if settings.db_path.exists():
        _core.backup_sqlite(settings.db_path, backup)
    return backup


def list_users(conn: sqlite3.Connection) -> list[dict]:
    """Return all users.

    Mirrors server.py:336-338.
    """
    return _core.list_users(conn)


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str,
    actor: str,
) -> dict:
    """Create a new local user.

    Mirrors core.py:create_user (server.py does not expose a /users/create
    endpoint in Phase 2, but the stub is kept for completeness).
    """
    _core.create_user(conn, username, password, role)
    conn.commit()
    return {"ok": True}


def update_user_role(
    conn: sqlite3.Connection,
    username: str,
    *,
    role: str,
    actor: str,
) -> None:
    """Update a user's role.

    Mirrors server.py:582-593.
    Raises KeyError if user not found (caller maps to 400 via ValueError).
    Raises ValueError if role is invalid.
    """
    try:
        _core.set_user_role(
            conn,
            username,
            role,
            actor=actor,
            actor_role="Admin",
        )
    except KeyError as exc:
        # Convert KeyError to ValueError so the global handler returns 400
        raise ValueError(str(exc)) from exc
    conn.commit()


def _clear_cicd_business_data(conn: sqlite3.Connection) -> None:
    """Clear CICD business tables added by the FastAPI rewrite.

    release_system.core.clear_business_data predates the App↔CICD rewrite and
    preserves these tables, so the new admin service clears them explicitly.
    """
    conn.execute("DELETE FROM cicd_task_requests")
    conn.execute("DELETE FROM cicd_notifications")
    conn.execute("DELETE FROM cicd_tasks")


def delete_user(conn: sqlite3.Connection, username: str, *, actor: str) -> None:
    """Delete a user.

    No direct equivalent in Phase-2 server.py (admin can only set-role and
    clear-db). Stub retained for the service layer; raises NotImplementedError
    until the admin UI needs it.
    """
    raise NotImplementedError


def clear_business_data(
    conn: sqlite3.Connection,
    *,
    password: str,
    actor: str,
) -> str:
    """Clear all business data after verifying admin password.

    Mirrors server.py:557-580.
    Raises AuthzError if password is missing or wrong.
    Returns the backup filename (stem).
    """
    from app.api.errors import AuthzError

    if not password:
        raise AuthzError("清空数据库需要重新输入 admin 密码")

    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (actor,)
    ).fetchone()
    if not row or not _core.verify_password(password, row["password_hash"]):
        raise AuthzError("admin 密码不正确")

    # Release the connection before backup copy (matches server.py:_close_conn)
    conn.close()
    backup = _backup_database()

    # Re-open a fresh connection for the clear operation
    from app.db.connection import connect

    fresh_conn = connect(settings.db_path)
    try:
        _core.clear_business_data(fresh_conn, user=actor, role="Admin")
        _clear_cicd_business_data(fresh_conn)
        fresh_conn.commit()
    finally:
        fresh_conn.close()

    return backup.name


def delete_app(
    conn: sqlite3.Connection,
    app_id: str,
    *,
    confirm: str,
    actor: str,
) -> dict:
    """Delete an app after confirmation.

    Mirrors server.py:749-757.
    Raises RuntimeError if confirm != app_id.
    Returns the deleted app dict plus backup filename.
    """
    if confirm != app_id:
        raise RuntimeError("删除确认必须等于 app_id")

    # Release before backup (mirrors server.py:_close_conn before backup)
    conn.close()
    backup = _backup_database()

    from app.db.connection import connect

    fresh_conn = connect(settings.db_path)
    try:
        fresh_conn.execute(
            "DELETE FROM cicd_task_requests WHERE app_id = ? OR task_id = ?",
            (app_id, app_id),
        )
        fresh_conn.execute("DELETE FROM cicd_tasks WHERE app_id = ?", (app_id,))
        deleted = _core.delete_app(fresh_conn, app_id, user=actor, role="Admin")
        fresh_conn.commit()
    finally:
        fresh_conn.close()

    return {"ok": True, "deleted": deleted, "backup": backup.name}
