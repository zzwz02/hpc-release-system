"""App-backed release_decision → CICD status request tests."""
from __future__ import annotations

import json

import pytest

from app.db.connection import transaction
from app.repositories import apps_repo
from app.services import app_service, cicd_service
from app.timeutil import beijing_timestamp
from tests.conftest import seed_release


APP_ID = "cicd_da_app"


def seed_app(conn, app_id: str = APP_ID) -> str:
    apps_repo.save_app(conn, {
        "id": app_id,
        "git_url": "ssh://gerrit/PDE/HPC/hpc_da",
        "git_branch": "main",
        "aliases": ["DecisionApp"],
        "created_by": "test",
        "created_at": beijing_timestamp(),
    })
    conn.commit()
    return app_id


def sync(conn, decision: str, *, old_status: str = "Running", app_id: str = APP_ID):
    with transaction(conn):
        return cicd_service.sync_decision_to_cicd(
            conn,
            app_id,
            decision,
            submitter="rm",
            current_status_override=old_status,
        )


def pending_status_requests(conn, app_id: str = APP_ID) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM cicd_task_requests
        WHERE app_id = ? AND task_id = ? AND status = 'pending'
          AND request_type = 'modify'
        ORDER BY id
        """,
        (app_id, app_id),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["_payload"] = json.loads(d["payload"] or "{}")
        result.append(d)
    return result


class TestDecisionMapping:
    @pytest.mark.parametrize(
        "decision,old_status,new_status",
        [
            ("release", "Stopped", "Running"),
            ("cicd_only", "Stopped", "Running"),
            ("stopped", "Running", "Stopped"),
        ],
    )
    def test_decision_creates_pending_status_request(self, temp_db, decision, old_status, new_status):
        seed_app(temp_db)
        req = sync(temp_db, decision, old_status=old_status)
        assert req is not None
        assert req["task_id"] == APP_ID
        assert req["app_id"] == APP_ID
        assert req["origin"] == "release_decision_sync"
        assert req["request_type"] == "modify"
        payload = req["payload"] if isinstance(req["payload"], dict) else json.loads(req["payload"])
        assert payload["status"] == {"old": old_status, "new": new_status}

    def test_same_status_is_noop(self, temp_db):
        seed_app(temp_db)
        assert sync(temp_db, "release", old_status="Running") is None

    def test_missing_app_is_noop(self, temp_db):
        assert sync(temp_db, "stopped", old_status="Running", app_id="missing") is None

    def test_pending_status_request_blocks_duplicates(self, temp_db):
        seed_app(temp_db)
        first = sync(temp_db, "stopped", old_status="Running")
        second = sync(temp_db, "stopped", old_status="Running")
        assert first is not None
        assert second is None
        assert len(pending_status_requests(temp_db)) == 1


class TestStatusLock:
    def test_user_modify_cannot_set_status(self, temp_db):
        seed_app(temp_db)
        with pytest.raises(RuntimeError):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"status": {"old": "Running", "new": "Stopped"}},
                submitter="owner",
                submitter_role="Owner",
                source="app_workbench",
            )

    def test_user_modify_app_cicd_fields_succeeds(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "", "new": "owner tweak"}},
            submitter="owner",
            submitter_role="Owner",
            source="app_workbench",
        )
        assert req["status"] == "pending"
        assert req["task_id"] == APP_ID

    def test_create_with_initial_status_is_allowed_for_valid_app(self, temp_db):
        seed_app(temp_db)
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="create",
            payload={
                "app_name": "DecisionApp",
                "repo_type": "git",
                "repo_name": "ssh://gerrit/PDE/HPC/hpc_da",
                "branch": "main",
                "owner_username": "owner",
                "status": "Running",
            },
            submitter="owner",
            submitter_role="Owner",
        )
        assert req["request_type"] == "create"
        assert req["task_id"] == APP_ID


class TestUpdateSnapshotIntegration:
    def test_decision_change_adds_app_backed_cicd_sync(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "stopped"}},
        )
        assert response["cicd_sync"]["created"] is True
        req = response["cicd_sync"]["request"]
        assert req["task_id"] == app_id
        assert req["app_id"] == app_id
        payload = req["payload"] if isinstance(req["payload"], dict) else json.loads(req["payload"])
        assert payload["status"]["new"] == "Stopped"

    def test_same_decision_does_not_add_cicd_sync(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        import release_system.core as core

        rel = core.get_release(temp_db, release_id)
        app_id = next(iter(rel["snapshots"]))
        response = app_service.update_snapshot(
            temp_db,
            release_id,
            app_id,
            user="rm",
            role="RM",
            fields={"snapshot": {"release_decision": "release"}},
        )
        assert "cicd_sync" not in response
