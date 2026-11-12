from __future__ import annotations

import pytest

from app.config import settings
from app.db.connection import connect, reset_init_state
from app.services import admin_service, cycle_service
from tests.conftest import seed_app, seed_cicd_request, seed_cycle, seed_signup


@pytest.fixture()
def admin_db(tmp_path, monkeypatch):
    """A temp DB registered as settings.db_path (admin_service reopens it)."""
    db_path = tmp_path / "admin.db"
    monkeypatch.setattr(settings, "db_path", db_path)
    reset_init_state()
    conn = connect(db_path)
    admin_service.create_user(
        conn, username="admin", password="admin-pass", role="Admin", actor="admin"
    )
    try:
        yield conn, db_path
    finally:
        try:
            conn.close()
        except Exception:
            pass
        reset_init_state()


def reopen(db_path):
    reset_init_state()
    return connect(db_path)


def test_clear_business_data_clears_cycles_apps_and_cicd_tables(admin_db):
    conn, db_path = admin_db
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    seed_signup(conn, release_id, app_id)
    seed_cicd_request(conn, app_id)

    backup_name = admin_service.clear_business_data(conn, password="admin-pass", actor="admin")

    check = reopen(db_path)
    try:
        assert backup_name.startswith("release_system_admin_backup_")
        assert check.execute("SELECT COUNT(*) FROM users WHERE username='admin'").fetchone()[0] == 1
        for table in (
            "apps",
            "releases",
            "entries",
            "app_info_versions",
            "app_dependencies",
            "cicd_task_requests",
            "cicd_notifications",
        ):
            assert check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        check.close()


def test_clear_business_data_requires_the_admin_password(admin_db):
    from app.api.errors import AuthzError

    conn, _ = admin_db
    with pytest.raises(AuthzError):
        admin_service.clear_business_data(conn, password="wrong", actor="admin")


def test_delete_app_clears_cicd_requests_and_entries_but_keeps_notifications(admin_db):
    conn, db_path = admin_db
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    seed_signup(conn, release_id, app_id)
    seed_cicd_request(conn, app_id)
    conn.execute(
        "INSERT INTO cicd_notifications(username, last_visited_at) VALUES ('rm', '2026-01-01 00:00:00')"
    )
    conn.commit()

    result = admin_service.delete_app(conn, app_id, confirm=app_id, actor="admin")
    assert result["ok"] is True

    check = reopen(db_path)
    try:
        assert check.execute("SELECT COUNT(*) FROM apps").fetchone()[0] == 0
        assert check.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert check.execute("SELECT COUNT(*) FROM app_info_versions").fetchone()[0] == 0
        assert check.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE app_id = ?", (app_id,)
        ).fetchone()[0] == 0
        assert check.execute(
            "SELECT COUNT(*) FROM cicd_notifications WHERE username = 'rm'"
        ).fetchone()[0] == 1
    finally:
        check.close()


def test_delete_app_refuses_an_app_that_took_part_in_a_published_cycle(admin_db):
    conn, _ = admin_db
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    seed_signup(conn, release_id, app_id)
    from tests.conftest import close_qa

    close_qa(conn, release_id)
    cycle_service.publish(conn, release_id, user="rm", role="RM")

    with pytest.raises(RuntimeError, match="published releases"):
        admin_service.delete_app(conn, app_id, confirm=app_id, actor="admin")


def test_delete_app_requires_the_confirmation_to_match(admin_db):
    conn, _ = admin_db
    app_id = seed_app(conn)
    with pytest.raises(RuntimeError, match="删除确认"):
        admin_service.delete_app(conn, app_id, confirm="nope", actor="admin")
