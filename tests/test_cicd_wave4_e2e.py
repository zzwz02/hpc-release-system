"""End-to-end CICD checks after the app-backed cutover.

The tests are intentionally offline: Gerrit fetches use the service fake, and
HTTP paths run against a temporary SQLite DB through FastAPI dependency
overrides.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.connection import connect as app_connect
from app.db.connection import reset_init_state
from app.deps import get_db, require_login
from app.main import create_app
from app.repositories import apps_repo
from app.services import cicd_service
from app.timeutil import beijing_timestamp
from release_system import core
from tests.conftest import seed_release

_SHORT_NAME = "hpc_w4e2e"
_BRANCH = "w4-test"
_OFFICIAL_NAME = "W4E2EApp"
_APP_ID = "w4e2eapp"
_RESOLVED_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_w4e2e"

_CREATE_BODY = {
    "official_name": _OFFICIAL_NAME,
    "repo_type": "git",
    "repo_name": _SHORT_NAME,
    "branch": _BRANCH,
    "app_version": "2.5",
    "build_product": ["maca"],
    "community_artifact": ["image"],
    "build_image": "hpc/w4e2e:latest",
    "test_timeout": 40,
    "notes": "wave4 e2e",
    "cicd_repo_type": "git",
    "cicd_community_artifact": "image",
    "cicd_build_image": "hpc/w4e2e:latest",
    "cicd_test_timeout": "40",
    "cicd_notes": "wave4 app config",
}


def _payload(row: dict) -> dict:
    value = row.get("payload") or {}
    return value if isinstance(value, dict) else json.loads(value)


def _task_table_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


def _seed_db(db_path: Path, tmp_dir: Path) -> None:
    reset_init_state()
    conn = app_connect(db_path)
    try:
        seed_release(conn, tmp_path=tmp_dir)
    finally:
        conn.close()


def _make_client(db_path: Path, *, role: str = "RM", username: str = "rm") -> TestClient:
    app = create_app()

    def override_db():
        conn = app_connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: {
        "username": username,
        "role": role,
        "display_name": username,
    }
    return TestClient(app, raise_server_exceptions=False)


def _insert_app(conn, app_id: str = "app1") -> None:
    apps_repo.save_app(
        conn,
        {
            "id": app_id,
            "git_url": f"repo/{app_id}",
            "git_branch": "main",
            "aliases": [app_id],
            "created_by": "test",
            "created_at": beijing_timestamp(),
        },
    )
    conn.commit()


class TestFakeAppInfoPreview:
    def test_service_preview_returns_identity_and_parsed_fields(self):
        fake_fetch = cicd_service.make_fake_app_info_fetch(
            app_name="FakeW4App",
            app_version="2.5",
            x86_chips=["C500", "N100"],
            arm_chips=["T5000"],
            python_label="3.11",
            pytorch_label="2.3",
            commit_id="fakew4commit0000000000000000000000000000",
        )

        result = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fake_fetch,
        )

        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH
        assert result["app_info_unavailable"] is False
        assert result["app_version"] == "2.5"
        assert "C500" in result["x86_chips"]
        assert "T5000" in result["arm_chips"]
        assert result["python_label"] == "3.11"
        assert result["pytorch_label"] == "2.3"
        assert result["commit_id"] == "fakew4commit0000000000000000000000000000"
        assert result["parsed"]["app_version"] == "2.5"

    def test_http_preview_uses_same_response_shape(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)
        fake_fetch = cicd_service.make_fake_app_info_fetch(app_version="3.0")

        with _make_client(db_path, role="RM", username="rm") as client:
            with patch("app.integrations.gerrit.fetch_app_info", fake_fetch):
                resp = client.post(
                    "/api/cicd/apps/fetch-preview",
                    json={"repo_type": "git", "repo_name": _SHORT_NAME, "branch": _BRANCH},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["git_url"] == _RESOLVED_URL
        assert body["git_branch"] == _BRANCH
        assert body["app_version"] == "3.0"
        assert "parsed" in body


class TestCicdFirstHttpLifecycle:
    def test_http_create_and_approve_stay_app_backed(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)

        with _make_client(db_path, role="RM", username="rm") as client:
            create_resp = client.post("/api/cicd/apps/new", json=_CREATE_BODY)
            assert create_resp.status_code == 200
            created = create_resp.json()
            req_id = created["request"]["id"]

            approve_resp = client.post(
                "/api/cicd/requests/approve",
                json={"request_id": req_id, "approval_mode": "immediate"},
            )
            assert approve_resp.status_code == 200
            approved = approve_resp.json()["request"]

        assert created["app_id"] == _APP_ID
        assert created["request"]["app_id"] == _APP_ID
        assert created["request"]["task_id"] == _APP_ID
        assert _payload(created["request"])["app_id"] == _APP_ID
        assert approved["app_id"] == _APP_ID
        assert approved["task_id"] == _APP_ID

        conn = app_connect(db_path)
        try:
            app = apps_repo.get_app(conn, _APP_ID)
            assert app is not None
            assert app["git_url"] == _RESOLVED_URL
            assert app["git_branch"] == _BRANCH
            assert app["cicd_build_image"] == "hpc/w4e2e:latest"
            assert _task_table_count(conn) == 0
        finally:
            conn.close()

    def test_create_with_parsed_app_info_marks_snapshot_owner_confirmed(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        fake_fetch = cicd_service.make_fake_app_info_fetch(app_version="4.4")
        preview = cicd_service.preview_cicd_app_info(
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter_role="RM",
            _fetch_fn=fake_fetch,
        )

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_SHORT_NAME,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            payload=_CREATE_BODY,
            app_info_parsed=preview["parsed"],
            app_info_commit_id=preview["commit_id"],
        )

        assert result["request"]["app_id"] == _APP_ID
        snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
        assert snap["release_decision"] == "cicd_only"
        assert snap["owner_confirmed"] is True
        assert snap["version"] == "4.4"
        assert snap["app_info"]["source_type"] == "cicd_workbench"
        assert _task_table_count(temp_db) == 0

    def test_http_duplicate_create_rejects_after_pending_request(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)

        with _make_client(db_path, role="RM", username="rm") as client:
            first = client.post("/api/cicd/apps/new", json=_CREATE_BODY)
            second = client.post("/api/cicd/apps/new", json=_CREATE_BODY)

        assert first.status_code == 200
        assert second.status_code == 400
        assert "待审批" in second.text


class TestCicdPermissions:
    def test_admin_cannot_create_cicd_first_app(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)

        with _make_client(db_path, role="Admin", username="admin") as client:
            resp = client.post("/api/cicd/apps/new", json=_CREATE_BODY)

        assert resp.status_code == 403

    def test_owner_create_allowed_but_approval_forbidden(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)

        with _make_client(db_path, role="Owner", username="owner") as owner_client:
            create_resp = owner_client.post(
                "/api/cicd/apps/new",
                json={**_CREATE_BODY, "official_name": "OwnerCreateApp", "repo_name": "hpc_owner_create"},
            )
            assert create_resp.status_code == 200
            req_id = create_resp.json()["request"]["id"]

            approve_resp = owner_client.post(
                "/api/cicd/requests/approve",
                json={"request_id": req_id, "approval_mode": "immediate"},
            )

        assert approve_resp.status_code == 403

    def test_spd_cannot_approve(self, db_path, tmp_dir):
        _seed_db(db_path, tmp_dir)
        conn = app_connect(db_path)
        try:
            _insert_app(conn, "spd-app")
            req = cicd_service.submit_request(
                conn,
                task_id="spd-app",
                request_type="create",
                payload={
                    "app_name": "SpdApp",
                    "repo_type": "git",
                    "repo_name": "repo/spd-app",
                    "branch": "main",
                    "build_product": [],
                    "community_artifact": [],
                    "build_image": "",
                    "test_timeout": 40,
                    "owner_username": "owner",
                    "status": "Running",
                    "notes": "",
                },
                submitter="owner",
                submitter_role="Owner",
            )
        finally:
            conn.close()

        with _make_client(db_path, role="SPD", username="spd") as client:
            resp = client.post(
                "/api/cicd/requests/approve",
                json={"request_id": req["id"], "approval_mode": "immediate"},
            )

        assert resp.status_code == 403


class TestDecisionSyncAndTime:
    def test_decision_sync_request_is_app_backed(self, temp_db):
        _insert_app(temp_db, "sync-app")

        req = cicd_service.sync_decision_to_cicd(
            temp_db,
            "sync-app",
            "stopped",
            submitter="rm",
            current_status_override="Running",
        )

        assert req is not None
        assert req["app_id"] == "sync-app"
        assert req["task_id"] == "sync-app"
        assert req["origin"] == "release_decision_sync"
        assert _payload(req)["status"] == {"old": "Running", "new": "Stopped"}
        assert _task_table_count(temp_db) == 0

    def test_request_timestamps_are_naive_beijing_strings(self, temp_db):
        _insert_app(temp_db, "time-app")

        req = cicd_service.submit_request(
            temp_db,
            task_id="time-app",
            request_type="create",
            payload={
                "app_name": "TimeApp",
                "repo_type": "git",
                "repo_name": "repo/time",
                "branch": "main",
                "build_product": [],
                "community_artifact": [],
                "build_image": "",
                "test_timeout": 40,
                "owner_username": "rm",
                "status": "Running",
                "notes": "",
            },
            submitter="rm",
            submitter_role="RM",
        )

        submitted_at = req["submitted_at"]
        assert len(submitted_at) == len("2026-01-01 00:00:00")
        assert "T" not in submitted_at
        assert "+" not in submitted_at
        assert submitted_at.endswith("Z") is False
        assert _task_table_count(temp_db) == 0
