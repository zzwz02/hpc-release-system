"""App-backed CICD request lifecycle tests.

``apps.id`` is the CICD identity: requests carry ``cicd_task_requests.app_id``
(``task_id`` holds the same id for the existing API field), and a new App row
appears only when its create request takes effect (rule 7).  The legacy
``cicd_tasks`` table may still exist in the schema, but no business rows are
written there.
"""
from __future__ import annotations

import pytest

from app.repositories import apps_repo
from app.services import cicd_service

APP_NAME = "RulingBCTestApp"
APP_ID = "rulingbctestapp"
REPO_NAME = "hpc_ruling_bc"
BRANCH = "main"

CREATE_CONFIG = {
    "app_version": "1.0",
    "build_product": ["maca"],
    "community_artifact": ["image"],
    "build_image": "hpc/ruling-bc:latest",
    "test_timeout": 40,
    "notes": "ruling-bc test",
}


def task_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


def submit_create(conn, *, username: str = "rm", role: str = "RM") -> dict:
    """Submit a create request for the test App (no App row yet)."""
    return cicd_service.submit_new_app(
        conn,
        official_name=APP_NAME,
        repo_type="git",
        repo_name=REPO_NAME,
        branch=BRANCH,
        doc_target="manual",
        app_type="分子动力学",
        config=dict(CREATE_CONFIG),
        submitter=username,
        submitter_role=role,
        submitter_display=f"{role} display",
    )["request"]


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
        with pytest.raises(PermissionError):
            submit_create(temp_db, username=role.lower(), role=role)


class TestAppBackedSubmit:
    @pytest.mark.parametrize("role,username", [("RM", "rm"), ("Owner", "owner_test")])
    def test_allowed_submitters_create_pending_request_without_an_app(
        self, temp_db, role, username
    ):
        req = submit_create(temp_db, username=username, role=role)
        assert req["status"] == "pending"
        assert req["request_type"] == "create"
        assert req["app_id"] is None
        assert req["reviewer"] == ""
        assert req["is_self_approved"] == 0
        # Rule 7: the App appears only when the request takes effect.
        assert apps_repo.list_apps(temp_db) == []
        assert task_count(temp_db) == 0

    def test_duplicate_create_for_the_same_identity_is_rejected(self, temp_db):
        submit_create(temp_db)
        with pytest.raises(RuntimeError, match="未完成的新增 App 申请"):
            submit_create(temp_db)

    def test_modify_request_needs_an_existing_app(self, temp_db):
        with pytest.raises(RuntimeError, match="有效 App"):
            cicd_service.submit_request(
                temp_db,
                task_id="missing-app",
                request_type="modify",
                payload={"notes": {"old": "", "new": "x"}},
                submitter="rm",
                submitter_role="RM",
                source="app_workbench",
            )

    def test_create_through_submit_request_is_refused(self, temp_db):
        with pytest.raises(RuntimeError, match="新增 App"):
            cicd_service.submit_request(
                temp_db,
                task_id=None,
                request_type="create",
                payload={"app_name": APP_NAME},
                submitter="rm",
                submitter_role="RM",
                source="app_workbench",
            )


class TestAppBackedApproval:
    def test_rm_approval_creates_the_app_without_a_task_row(self, temp_db):
        req = submit_create(temp_db, username="owner_test", role="Owner")
        approved = approve(temp_db, req["id"], reviewer="rm")
        assert approved["status"] == "approved"
        assert approved["reviewer"] == "rm"
        assert approved["app_id"] == APP_ID
        assert approved["task_id"] == APP_ID
        assert approved["is_self_approved"] == 0
        assert task_count(temp_db) == 0

        app = apps_repo.get_app(temp_db, APP_ID)
        assert app["official_name"] == APP_NAME
        assert app["cicd_status"] == "running"
        assert app["owners"] == ["owner_test"]
        assert app["git_branch"] == BRANCH

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

    def test_dispatch_to_spd_creates_the_app_only_on_delivery(self, temp_db):
        req = submit_create(temp_db)
        dispatched = approve(temp_db, req["id"], reviewer="rm", approval_mode="dispatch_spd")
        assert dispatched["status"] == "approved"
        assert dispatched["delivery_status"] == "pending"
        assert apps_repo.get_app(temp_db, APP_ID) is None

        delivered = cicd_service.deliver_request(
            temp_db, req["id"], deliverer="spd", deliverer_role="SPD"
        )
        assert delivered["delivery_status"] == "delivered"
        assert apps_repo.get_app(temp_db, APP_ID)["cicd_build_image"] == "hpc/ruling-bc:latest"

    def test_rejected_create_leaves_no_app_behind(self, temp_db):
        req = submit_create(temp_db)
        rejected = cicd_service.reject_request(
            temp_db, req["id"], reviewer="rm", reviewer_role="RM", review_note="不需要"
        )
        assert rejected["status"] == "rejected"
        assert apps_repo.list_apps(temp_db) == []
        # The identity is free again after a rejection.
        assert submit_create(temp_db)["status"] == "pending"

    def test_admin_cannot_approve(self, temp_db):
        req = submit_create(temp_db, username="owner_test", role="Owner")
        with pytest.raises(PermissionError):
            approve(temp_db, req["id"], reviewer="admin", role="Admin")


class TestAppBackedModify:
    def test_config_modify_applies_on_approval(self, temp_db):
        approve(temp_db, submit_create(temp_db)["id"])
        req = cicd_service.submit_request(
            temp_db,
            task_id=APP_ID,
            request_type="modify",
            payload={"notes": {"old": "ruling-bc test", "new": "changed"}},
            submitter="rm",
            submitter_role="RM",
            source="app_workbench",
        )
        assert req["app_id"] == APP_ID
        approve(temp_db, req["id"])
        assert apps_repo.get_app(temp_db, APP_ID)["cicd_notes"] == "changed"

    def test_config_modify_must_come_from_the_app_workbench(self, temp_db):
        approve(temp_db, submit_create(temp_db)["id"])
        with pytest.raises(RuntimeError, match="App 工作台"):
            cicd_service.submit_request(
                temp_db,
                task_id=APP_ID,
                request_type="modify",
                payload={"notes": {"old": "", "new": "x"}},
                submitter="rm",
                submitter_role="RM",
            )


class TestRemovedLegacyTaskOperations:
    def test_transfer_owner_service_removed(self):
        assert not hasattr(cicd_service, "transfer_owner")

    def test_release_decision_sync_is_removed(self):
        assert not hasattr(cicd_service, "sync_decision_to_cicd")
