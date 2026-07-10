"""Admin service — user/role management and system maintenance.

Full port of the admin-related handlers in server.py and release_system/core.py.
Ruling-C (Admin out of release business) is Phase 4; this service ports existing
behavior verbatim.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.api.errors import AuthzError
from app.config import settings
from app.db.connection import backup_sqlite, connect, transaction
from app.domain.permissions import ROLES
from app.repositories import apps_repo, users_repo
from app.repositories.audit_repo import log_audit
from app.services import auth_service
from app.timeutil import beijing_timestamp


def _backup_database() -> Path:
    """Create a timestamped SQLite backup file beside the main DB.

    Mirrors server.py:backup_database (server.py:1416-1421).
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = settings.db_path.parent / f"release_system_admin_backup_{stamp}.sqlite"
    if settings.db_path.exists():
        backup_sqlite(settings.db_path, backup)
    return backup


def list_users(conn: sqlite3.Connection) -> list[dict]:
    """Return all users.

    Mirrors server.py:336-338.
    """
    return users_repo.list_users(conn)


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str,
    actor: str,
) -> dict:
    """Create (or reset) a local user.

    Mirrors core.py:create_user (server.py does not expose a /users/create
    endpoint in Phase 2, but the stub is kept for completeness).
    """
    with transaction(conn):
        conn.execute(
            "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, "
            "role=excluded.role",
            (username, auth_service.hash_password(password), role),
        )
    return {"ok": True}


def update_user_role(
    conn: sqlite3.Connection,
    username: str,
    *,
    role: str,
    actor: str,
) -> None:
    """Update a user's role.

    Mirrors server.py:582-593 and core.py:set_user_role.
    Raises ValueError if role is invalid or user not found (global handler → 400).
    """
    if role not in ROLES:
        raise ValueError(f"无效角色：{role}，合法值为 {sorted(ROLES)}")
    existing = users_repo.get_user(conn, username)
    if not existing:
        raise ValueError(f"用户不存在：{username}")
    old_role = existing["role"]
    with transaction(conn):
        users_repo.update_user_role(conn, username, role=role)
        log_audit(
            conn,
            f"修改用户角色：{username}  {old_role} → {role}",
            ts=beijing_timestamp(),
            user=actor,
            role="Admin",
            event="set_user_role",
            detail=[{"field": "role", "label": "角色", "old": old_role, "new": role}],
        )


def _clear_cicd_business_data(conn: sqlite3.Connection) -> None:
    """Clear CICD business tables owned by the current app-backed CICD flow.

    The legacy clear_business_data predates the App-backed CICD flow and
    preserves these tables, so the new admin service clears them explicitly.
    """
    conn.execute("DELETE FROM cicd_task_requests")
    conn.execute("DELETE FROM cicd_notifications")


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

    Mirrors server.py:557-580 and core.py:clear_business_data.
    Raises AuthzError if password is missing or wrong.
    Returns the backup filename (stem).
    """
    if not password:
        raise AuthzError("清空数据库需要重新输入 admin 密码")

    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (actor,)
    ).fetchone()
    if not row or not auth_service.verify_password(password, row["password_hash"]):
        raise AuthzError("admin 密码不正确")

    # Release the connection before backup copy (matches server.py:_close_conn)
    conn.close()
    backup = _backup_database()

    # Re-open a fresh connection for the clear operation
    fresh_conn = connect(settings.db_path)
    try:
        with transaction(fresh_conn):
            fresh_conn.execute("DELETE FROM artifacts")
            fresh_conn.execute("DELETE FROM qa_logs")
            fresh_conn.execute("DELETE FROM snapshots")
            fresh_conn.execute("DELETE FROM releases")
            fresh_conn.execute("DELETE FROM apps")
            fresh_conn.execute("DELETE FROM release_schedule")
            fresh_conn.execute("DELETE FROM wiki_images")
            fresh_conn.execute("DELETE FROM wiki_articles")
            fresh_conn.execute("DELETE FROM audit")
            _clear_cicd_business_data(fresh_conn)
            auth_service.ensure_default_users(fresh_conn)
            log_audit(
                fresh_conn,
                "数据库已清空，默认账号已保留",
                ts=beijing_timestamp(),
                user=actor,
                role="Admin",
            )
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

    Mirrors server.py:749-757 and core.py:delete_app.
    Raises RuntimeError if confirm != app_id.
    Returns the deleted app dict plus backup filename.
    """
    if confirm != app_id:
        raise RuntimeError("删除确认必须等于 app_id")

    # Release before backup (mirrors server.py:_close_conn before backup)
    conn.close()
    backup = _backup_database()

    fresh_conn = connect(settings.db_path)
    try:
        app = apps_repo.get_app(fresh_conn, app_id)
        if not app:
            raise KeyError(f"Unknown app: {app_id}")
        locked_releases = apps_repo.locked_releases_for_app(fresh_conn, app_id)
        if locked_releases:
            raise RuntimeError(
                f"App is used by locked releases and cannot be deleted: {', '.join(locked_releases)}"
            )
        with transaction(fresh_conn):
            fresh_conn.execute(
                "DELETE FROM cicd_task_requests WHERE app_id = ? OR task_id = ?",
                (app_id, app_id),
            )
            affected_releases = apps_repo.affected_release_ids_for_app(fresh_conn, app_id)
            apps_repo.delete_draft_artifacts_for_releases(fresh_conn, affected_releases)
            apps_repo.delete_app(fresh_conn, app_id)
            log_audit(
                fresh_conn,
                f"删除 app：{app_id}",
                ts=beijing_timestamp(),
                user=actor,
                role="Admin",
                app_id=app_id,
                event="delete_app",
            )
    finally:
        fresh_conn.close()

    return {"ok": True, "deleted": app, "backup": backup.name}
