"""App-backed CICD request lifecycle tests.

After the app-backed cutover, `apps.id` is the CICD identity.  New requests use
`cicd_task_requests.app_id`; `task_id` is stored as the same app id for the
existing API field.  The legacy `cicd_tasks` table may still exist in the
schema, but no new business rows are created there.
"""
from __future__ import annotations

import pytest

from app.repositories import apps_repo
from app.services import cicd_service
from app.timeutil import beijing_timestamp


APP_ID = "cicd_bc_app"

CREATE_PAYLOAD = {
    "app_name": "RulingBCTestApp",
    "app_version": "1.0",
    "repo_type": "git",
    "repo_name": "ssh://gerrit/PDE/HPC/hpc_ruling_bc",
    "branch": "main",
    "build_product": ["maca"],
    "community_artifact": ["image"],
    "build_image": "hpc/ruling-bc:latest",
    "test_timeout": 40,
    "owner_username": "rm",
    "status": "Running",
    "notes": "ruling-bc test",
}


def seed_app(conn, app_id: str = APP_ID) -> str:
    apps_repo.save_app(conn, {
        "id": app_id,
        "git_url": "ssh://gerrit/PDE/HPC/hpc_ruling_bc",
        "git_branch": "main",
        "aliases": ["RulingBCTestApp"],
        "created_by": "test",
        "created_at": beijing_timestamp(),
    })
    conn.commit()
    return app_id


def task_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


def submit_create(conn, *, username: str = "rm", role: str = "RM") -> dict:
    app_id = seed_app(conn)
    return cicd_service.submit_request(
        conn,
        task_id=app_id,
        request_type="create",
        payload=CREATE_PAYLOAD,
        submitter=username,
        submitter_role=role,
        submitter_display=f"{role} display",
    )


def approve(conn, req_id: int, *, reviewer: str = "rm", role: str = "RM", **kwargs) -> dict:
    return cicd_service.approve_request(
        conn,
        req_id,
        reviewer=reviewer,
        reviewer_role=role,
        **kwargs,
    )


class TestRulingBCRoles:
    def test_cicd_create_roles_are_owner_and_rm(self):
        assert cicd_service.CICD_CREATE_ROLES == frozenset({"Owner", "RM"})

    def test_cicd_approver_roles_are_rm_only(self):
        assert cicd_service.CICD_APPROVER_ROLES == frozenset({"RM"})

    @pytest.mark.parametrize("role", ["Admin", "SPD", "Guest"])
    def test_non_create_roles_cannot_submit(self, temp_db, role):
        seed_app(temp_db)
        with pytest.raises(PermissionError):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="create",
                payload=CREATE_PAYLOAD,
                submitter=role.lower(),
                submitter_role=role,
            )


class TestAppBackedSubmit:
    @pytest.mark.parametrize("role,username", [("RM", "rm"), ("Owner", "owner_test")])
    def test_allowed_submitters_create_pending_app_request(self, temp_db, role, username):
        req = submit_create(temp_db, username=username, role=role)
        assert req["status"] == "pending"
        assert req["request_type"] == "create"
        assert req["task_id"] == APP_ID
        assert req["app_id"] == APP_ID
        assert req["reviewer"] == ""
        assert req["is_self_approved"] == 0
        assert task_count(temp_db) == 0

    def test_create_without_valid_app_is_rejected(self, temp_db):
        with pytest.raises(RuntimeError, match="有效 App"):
            cicd_service.submit_request(
                temp_db,
                task_id="missing-app",
                request_type="create",
                payload=CREATE_PAYLOAD,
                submitter="rm",
                submitter_role="RM",
            )


class TestAppBackedApproval:
    def test_rm_approval_marks_request_approved_without_creating_task_row(self, temp_db):
        req = submit_create(temp_db, username="owner_test", role="Owner")
        approved = approve(temp_db, req["id"], reviewer="rm")
        assert approved["status"] == "approved"
        assert approved["reviewer"] == "rm"
        assert approved["task_id"] == APP_ID
        assert approved["app_id"] == APP_ID
        assert approved["is_self_approved"] == 0
        assert task_count(temp_db) == 0

    def test_self_approval_sets_audit_flag(self, temp_db):
        req = submit_create(temp_db, username="rm", role="RM")
        approved = approve(temp_db, req["id"], reviewer="rm")
        assert approved["is_self_approved"] == 1

    def test_approval_applies_app_cicd_config(self, temp_db):
        req = submit_create(temp_db)
        approve(temp_db, req["id"], reviewer="rm")
        app = apps_repo.get_app(temp_db, APP_ID)
        assert app["cicd_repo_type"] == "git"
        assert app["cicd_community_artifact"] == "image"
        assert app["cicd_build_image"] == "hpc/ruling-bc:latest"
        assert app["cicd_test_timeout"] == "40"
        assert app["cicd_notes"] == "ruling-bc test"

    def test_dispatch_to_spd_applies_only_on_delivery(self, temp_db):
        req = submit_create(temp_db)
        dispatched = approve(
            temp_db,
            req["id"],
            reviewer="rm",
            approval_mode="dispatch_spd",
        )
        assert dispatched["status"] == "approved"
        assert dispatched["delivery_status"] == "pending"
        assert apps_repo.get_app(temp_db, APP_ID)["cicd_build_image"] == ""

        delivered = cicd_service.deliver_request(
            temp_db,
            req["id"],
            deliverer="spd",
            deliverer_role="SPD",
        )
        assert delivered["delivery_status"] == "delivered"
        assert apps_repo.get_app(temp_db, APP_ID)["cicd_build_image"] == "hpc/ruling-bc:latest"

    def test_admin_cannot_approve(self, temp_db):
        req = submit_create(temp_db, username="owner_test", role="Owner")
        with pytest.raises(PermissionError):
            approve(temp_db, req["id"], reviewer="admin", role="Admin")


class TestRemovedLegacyTaskOperations:
    def test_transfer_owner_service_removed(self):
        assert not hasattr(cicd_service, "transfer_owner")
