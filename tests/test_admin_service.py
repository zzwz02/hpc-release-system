from __future__ import annotations

import json

from app.config import settings
from app.db.connection import connect, reset_init_state
from app.services import admin_service
from release_system import core


def test_clear_business_data_clears_app_backed_cicd_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "admin-clear.db"
    monkeypatch.setattr(settings, "db_path", db_path)
    reset_init_state()
    conn = connect(db_path)
    try:
        core.create_user(conn, "admin", "admin-pass", "Admin")
        app_id = "app-clear-cicd"
        conn.execute(
            "INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (app_id, "repo/clear", "main", "[]", "admin", "2026-01-01 00:00:00"),
        )
        conn.execute(
            """
            INSERT INTO cicd_task_requests(
                task_id, app_id, request_type, payload, submitter, submitter_display,
                submitted_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_id,
                "modify",
                json.dumps({"notes": {"old": "", "new": "x"}}),
                "owner",
                "Owner",
                "2026-01-01 00:00:00",
                "pending",
            ),
        )
        conn.execute(
            "INSERT INTO cicd_notifications(username, last_visited_at) VALUES (?, ?)",
            ("rm", "2026-01-01 00:00:00"),
        )
        conn.commit()

        backup_name = admin_service.clear_business_data(
            conn,
            password="admin-pass",
            actor="admin",
        )

        reset_init_state()
        check = connect(db_path)
        try:
            assert backup_name.startswith("release_system_admin_backup_")
            assert check.execute(
                "SELECT COUNT(*) FROM users WHERE username = 'admin'"
            ).fetchone()[0] == 1
            assert check.execute("SELECT COUNT(*) FROM cicd_task_requests").fetchone()[0] == 0
            assert check.execute("SELECT COUNT(*) FROM cicd_notifications").fetchone()[0] == 0
        finally:
            check.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        reset_init_state()


def test_delete_app_clears_related_cicd_requests_but_keeps_notifications(tmp_path, monkeypatch):
    db_path = tmp_path / "admin-delete-app.db"
    monkeypatch.setattr(settings, "db_path", db_path)
    reset_init_state()
    conn = connect(db_path)
    try:
        core.create_user(conn, "admin", "admin-pass", "Admin")
        app_id = "app-delete-cicd"
        conn.execute(
            "INSERT INTO apps(id, git_url, git_branch, aliases_json, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (app_id, "repo/delete", "main", "[]", "admin", "2026-01-01 00:00:00"),
        )
        conn.execute(
            """
            INSERT INTO cicd_task_requests(
                task_id, app_id, request_type, payload, submitter, submitter_display,
                submitted_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_id,
                "modify",
                json.dumps({"notes": {"old": "", "new": "x"}}),
                "owner",
                "Owner",
                "2026-01-01 00:00:00",
                "pending",
            ),
        )
        conn.execute(
            "INSERT INTO cicd_notifications(username, last_visited_at) VALUES (?, ?)",
            ("rm", "2026-01-01 00:00:00"),
        )
        conn.commit()

        result = admin_service.delete_app(
            conn,
            app_id,
            confirm=app_id,
            actor="admin",
        )
        assert result["ok"] is True

        reset_init_state()
        check = connect(db_path)
        try:
            assert check.execute(
                "SELECT COUNT(*) FROM cicd_task_requests WHERE app_id = ?",
                (app_id,),
            ).fetchone()[0] == 0
            assert check.execute(
                "SELECT COUNT(*) FROM cicd_notifications WHERE username = 'rm'"
            ).fetchone()[0] == 1
        finally:
            check.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        reset_init_state()
