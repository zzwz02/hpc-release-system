"""Bulk synchronization is explicit, release-scoped and limited to signup."""
import datetime as dt
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.api.errors import ApiError
from app.config import settings
from app.deps import get_db, require_login
from app.integrations import gerrit
from app.main import create_app
from app.repositories import app_info_repo, apps_repo
from app.services import catalog_service
from tests.conftest import close_qa, close_signup, make_app_info, seed_app, seed_cycle, set_cycle_dates


def stub_source(monkeypatch):
    monkeypatch.setattr(gerrit, "head_commit", lambda *a, **kw: "a" * 40)
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *a, **kw: json.dumps(make_app_info(app_version="2.0")))


@pytest.mark.parametrize("now,allowed", [
    ("2026-11-13 01:59:59", True),
    ("2026-11-13 02:00:00", False),
    ("2026-11-13 02:00:01", False),
    ("2026-11-26 23:59:00", False),
])
def test_bulk_sync_obeys_signup_boundary_even_when_forced(temp_db, monkeypatch, now, allowed):
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    set_cycle_dates(temp_db, release_id, signup_deadline="2026-11-13 02:00",
                    qa_deadline="2026-11-26 23:59", release_date="2026-11-30")
    monkeypatch.setattr(timeutil, "beijing_now", lambda: dt.datetime.fromisoformat(now))
    stub_source(monkeypatch)
    if allowed:
        assert catalog_service.sync_all(temp_db, release_id=release_id, user="rm", force=True)["updated"] == 1
        assert app_info_repo.latest_version(temp_db, app_id)["raw"]["app_version"] == "2.0"
    else:
        monkeypatch.setattr(catalog_service, "_read_sync_app", lambda *a, **kw: pytest.fail("Unexpected source read"))
        with pytest.raises(ApiError, match="不允许全量同步") as error:
            catalog_service.sync_all(temp_db, release_id=release_id, user="rm", force=True)
        assert error.value.status_code == 409
        assert app_info_repo.latest_version(temp_db, app_id)["raw"]["app_version"] == "1.0"
        assert apps_repo.get_app(temp_db, app_id)["sync_checked_at"] == ""


def test_bulk_sync_requires_an_existing_selected_cycle_and_rejects_published_cycles(temp_db, monkeypatch):
    seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    monkeypatch.setattr(catalog_service, "_read_sync_app", lambda *a, **kw: pytest.fail("Unexpected source read"))
    for selected, code in [("", 400), ("missing", 404)]:
        with pytest.raises(ApiError) as error:
            catalog_service.sync_all(temp_db, release_id=selected, user="rm")
        assert error.value.status_code == code
    # A published cycle is forbidden even if its signup date is still future.
    temp_db.execute("UPDATE releases SET released_locked=1 WHERE id=?", (release_id,))
    temp_db.commit()
    with pytest.raises(ApiError, match="不允许全量同步"):
        catalog_service.sync_all(temp_db, release_id=release_id, user="rm")


def test_selected_closed_cycle_cannot_be_bypassed_by_another_open_cycle(temp_db, monkeypatch):
    seed_app(temp_db)
    closed = seed_cycle(temp_db, name="closed")
    open_cycle = seed_cycle(temp_db, name="open")
    close_signup(temp_db, closed)
    stub_source(monkeypatch)
    with pytest.raises(ApiError, match="不允许全量同步"):
        catalog_service.sync_all(temp_db, release_id=closed, user="rm")
    assert catalog_service.sync_all(temp_db, release_id=open_cycle, user="rm")["updated"] == 1


def test_bulk_sync_rechecks_edited_deadline_before_committing(temp_db_with_path, monkeypatch):
    conn, path = temp_db_with_path
    app_id = seed_app(conn)
    release_id = seed_cycle(conn)
    stub_source(monkeypatch)
    original = catalog_service._read_sync_app

    def read_and_close(app, **kwargs):
        result = original(app, **kwargs)
        from app.db.connection import connect
        other = connect(path)
        try:
            close_signup(other, release_id)
        finally:
            other.close()
        return result

    monkeypatch.setattr(catalog_service, "_read_sync_app", read_and_close)
    result = catalog_service.sync_all(conn, release_id=release_id, user="rm")
    assert result["updated"] == 0 and "报名截止" in result["error"]
    assert app_info_repo.latest_version(conn, app_id)["raw"]["app_version"] == "1.0"
    assert apps_repo.get_app(conn, app_id)["sync_checked_at"] == ""


def test_http_bulk_sync_is_rm_only_and_rechecks_before_launch_and_in_worker(temp_db_with_path, monkeypatch):
    conn, path = temp_db_with_path
    app_id = seed_app(conn)
    open_cycle = seed_cycle(conn, name="open")
    closed = seed_cycle(conn, name="closed")
    close_signup(conn, closed)
    qa_closed = seed_cycle(conn, name="qa_closed")
    close_qa(conn, qa_closed)
    monkeypatch.setattr(settings, "db_path", path)
    # Initial App fetch is a separate feature; even when enabled, startup
    # and status requests must never launch a recurring source sync.
    monkeypatch.setattr(settings, "app_info_sync_enabled", True)
    monkeypatch.setattr(catalog_service, "_read_sync_app", lambda *a, **kw: pytest.fail("Unexpected source read"))
    started = []

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            started.append(self.target)

    app = create_app()
    current = {"username": "rm", "role": "RM", "display_name": "RM"}
    app.dependency_overrides[require_login] = lambda: current

    def get_connection():
        yield conn

    app.dependency_overrides[get_db] = get_connection
    with TestClient(app, raise_server_exceptions=False) as client:
        # Replace only this service's thread factory, without changing TestClient's threads.
        monkeypatch.setattr(catalog_service, "threading", type("Threads", (), {"Lock": threading.Lock, "Thread": DeferredThread}))
        monkeypatch.setattr(catalog_service, "_sync_all_state", {"running": False, "release_id": ""})
        status = client.get("/api/app-info/sync-status")
        assert status.status_code == 200 and "daily" not in status.json()
        assert started == []
        for selected, code in [("", 400), ("missing", 404), (closed, 409), (qa_closed, 409)]:
            response = client.post("/api/app-info/sync-all", json={"release_id": selected})
            assert response.status_code == code, response.text
        for role in ["Owner", "QA", "Admin", "Guest"]:
            current["role"] = role
            assert client.post("/api/app-info/sync-all", json={"release_id": open_cycle}).status_code == 403
        assert started == []
        current["role"] = "RM"
        response = client.post("/api/app-info/sync-all", json={"release_id": open_cycle})
        assert response.status_code == 200, response.text
        assert response.json()["running"] is True
        assert response.json()["release_id"] == open_cycle
        assert len(started) == 1
        # A queued job also checks the phase when its worker actually starts.
        close_signup(conn, open_cycle)
        started[0]()
        state = client.get("/api/app-info/sync-status").json()["job"]
        assert state["running"] is False and "不允许全量同步" in state["summary"]["error"]
    assert app_info_repo.latest_version(conn, app_id)["raw"]["app_version"] == "1.0"
