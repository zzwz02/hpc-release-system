"""CICD-first app creation after the app-backed cutover.

The active model has no generated CICD task id.  `apps.id` is the canonical
identity, and `cicd_task_requests.task_id` is stored with the same app id for
the existing API field.
"""
from __future__ import annotations

import json

import pytest

from app.repositories import apps_repo
from app.services import cicd_service
from app.timeutil import beijing_timestamp
from release_system import core
from tests.conftest import seed_release

_REPO_SHORT = "hpc_w3cicd"
_BRANCH = "wave3"
_OFFICIAL_NAME = "W3CicdFirst"
_APP_ID = "w3cicdfirst"
_RESOLVED_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_w3cicd"

_BUILD_PAYLOAD: dict = {
    "app_version": "1.2.3",
    "build_product": ["maca"],
    "community_artifact": ["image"],
    "build_image": "hpc/w3cicd:latest",
    "test_timeout": 40,
    "notes": "wave3 create request",
    "cicd_repo_type": "git",
    "cicd_community_artifact": "image",
    "cicd_build_image": "hpc/w3cicd:latest",
    "cicd_test_timeout": "40",
    "cicd_notes": "wave3 app config",
}


def _payload(row: dict) -> dict:
    value = row.get("payload") or {}
    return value if isinstance(value, dict) else json.loads(value)


def _task_table_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_tasks").fetchone()[0]


def _request_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cicd_task_requests").fetchone()[0]


def _create_app(
    conn,
    tmp_dir,
    *,
    official_name: str = _OFFICIAL_NAME,
    repo_name: str = _REPO_SHORT,
    branch: str = _BRANCH,
    submitter: str = "rm",
    submitter_role: str = "RM",
    **payload_overrides,
) -> dict:
    seed_release(conn, tmp_path=tmp_dir)
    return cicd_service.cicd_first_new_app(
        conn,
        official_name=official_name,
        repo_type="git",
        repo_name=repo_name,
        branch=branch,
        submitter=submitter,
        submitter_role=submitter_role,
        submitter_display="",
        payload={**_BUILD_PAYLOAD, **payload_overrides},
    )


class TestIdentityDerivation:
    def test_short_name_expands_to_full_ssh_url(self):
        from app.identity import repo_to_git_identity

        url, branch = repo_to_git_identity("git", _REPO_SHORT, _BRANCH)
        assert url == _RESOLVED_URL
        assert branch == _BRANCH

    def test_absolute_url_passthrough(self):
        from app.identity import repo_to_git_identity

        url, branch = repo_to_git_identity("git", _RESOLVED_URL, _BRANCH)
        assert url == _RESOLVED_URL
        assert branch == _BRANCH

    def test_same_identity_normalises_short_and_full_url(self):
        from app.identity import same_identity

        assert same_identity(_REPO_SHORT, _BRANCH, _RESOLVED_URL, _BRANCH)
        assert not same_identity(_REPO_SHORT, "other", _RESOLVED_URL, _BRANCH)


class TestCicdFirstRoleGating:
    @pytest.mark.parametrize("role", ["Admin", "Guest", "SPD"])
    def test_non_create_roles_are_rejected(self, temp_db, tmp_dir, role):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(PermissionError):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="ForbiddenApp",
                repo_type="git",
                repo_name="hpc_forbidden",
                branch="main",
                submitter=role.lower(),
                submitter_role=role,
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0

    @pytest.mark.parametrize("role", ["Owner", "RM"])
    def test_owner_and_rm_can_submit(self, temp_db, tmp_dir, role):
        result = _create_app(
            temp_db,
            tmp_dir,
            official_name=f"{role}CreateApp",
            repo_name=f"hpc_{role.lower()}_create",
            submitter=role.lower(),
            submitter_role=role,
        )

        assert result["ok"] is True
        assert result["request"]["status"] == "pending"


class TestCicdFirstValidation:
    def test_empty_official_name_rejected_without_writes(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="app 名称|official_name"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="",
                repo_type="git",
                repo_name="hpc_valid",
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0
        assert _task_table_count(temp_db) == 0

    def test_empty_repo_rejected_without_writes(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="repo|身份"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="ValidName",
                repo_type="git",
                repo_name="",
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0
        assert _task_table_count(temp_db) == 0


class TestCicdFirstAppBackedLifecycle:
    def test_create_writes_app_and_app_backed_request(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )

        assert result["action"] == "created"
        assert result["app_id"] == _APP_ID
        assert result["git_url"] == _RESOLVED_URL
        assert result["git_branch"] == _BRANCH

        app = apps_repo.get_app(temp_db, _APP_ID)
        assert app is not None
        assert app["git_url"] == _RESOLVED_URL
        assert app["git_branch"] == _BRANCH
        assert app["cicd_build_image"] == "hpc/w3cicd:latest"

        req = result["request"]
        assert req["status"] == "pending"
        assert req["request_type"] == "create"
        assert req["app_id"] == _APP_ID
        assert req["task_id"] == _APP_ID
        assert _payload(req)["app_id"] == _APP_ID
        assert _task_table_count(temp_db) == 0

        snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
        assert snap["release_decision"] == "stopped"
        assert snap["owners"] == ["rm"]

    def test_approval_keeps_task_id_as_app_id_and_creates_no_task_row(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        req = result["request"]

        approved = cicd_service.approve_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
        )

        assert approved["status"] == "approved"
        assert approved["app_id"] == _APP_ID
        assert approved["task_id"] == _APP_ID
        assert approved["is_self_approved"] == 1
        assert _task_table_count(temp_db) == 0
        snap = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"][_APP_ID]
        assert snap["release_decision"] == "cicd_only"

    def test_dispatch_approval_applies_only_after_delivery(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        req = result["request"]

        approved = cicd_service.approve_request(
            temp_db,
            req["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-1",
        )

        assert approved["status"] == "approved"
        assert approved["delivery_status"] == "pending"
        assert _task_table_count(temp_db) == 0
        snap = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"][_APP_ID]
        assert snap["release_decision"] == "stopped"

        delivered = cicd_service.deliver_request(
            temp_db,
            req["id"],
            deliverer="spd",
            deliverer_role="SPD",
        )
        assert delivered["delivery_status"] == "delivered"
        assert delivered["task_id"] == _APP_ID
        assert _task_table_count(temp_db) == 0
        snap = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"][_APP_ID]
        assert snap["release_decision"] == "cicd_only"

    def test_duplicate_pending_create_is_rejected(self, temp_db, tmp_dir):
        _create_app(temp_db, tmp_dir)

        with pytest.raises(RuntimeError, match="待审批"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 1

    def test_duplicate_approved_create_is_rejected(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        cicd_service.approve_request(temp_db, result["request"]["id"], reviewer="rm", reviewer_role="RM")

        with pytest.raises(RuntimeError, match="已有 CICD 创建"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 1
        assert _task_table_count(temp_db) == 0

    def test_existing_app_without_create_request_is_associated(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        apps_repo.save_app(
            temp_db,
            {
                "id": "existing-app",
                "git_url": _RESOLVED_URL,
                "git_branch": _BRANCH,
                "aliases": ["Existing"],
                "created_by": "rm",
                "created_at": beijing_timestamp(),
            },
        )
        temp_db.commit()

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="Existing",
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            payload=_BUILD_PAYLOAD,
        )

        assert result["action"] == "associated"
        assert result["app_id"] == "existing-app"
        assert result["request"]["app_id"] == "existing-app"
        assert result["request"]["task_id"] == "existing-app"
        assert _task_table_count(temp_db) == 0


class TestCicdFirstRejectedLifecycle:
    def _submit_and_reject(self, temp_db, tmp_dir) -> tuple[dict, dict]:
        result = _create_app(temp_db, tmp_dir)
        rejected = cicd_service.reject_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
            review_note="镜像配置不符合要求",
        )
        return result, rejected

    def test_reject_keeps_app_row_and_stopped_snapshot(self, temp_db, tmp_dir):
        result, rejected = self._submit_and_reject(temp_db, tmp_dir)

        assert rejected["status"] == "rejected"
        assert apps_repo.get_app(temp_db, result["app_id"]) is not None
        snap = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"][result["app_id"]]
        assert snap["release_decision"] == "stopped"
        assert _task_table_count(temp_db) == 0

    def test_reject_exposes_onboarding_review_note(self, temp_db, tmp_dir):
        result, _ = self._submit_and_reject(temp_db, tmp_dir)

        state = cicd_service.cicd_first_onboarding_by_app(temp_db)[result["app_id"]]
        assert state["cicd_onboarding_status"] == "rejected_create"
        assert state["cicd_onboarding_review_note"] == "镜像配置不符合要求"

    def test_state_apps_include_rejected_reason(self, temp_db, tmp_dir):
        result, _ = self._submit_and_reject(temp_db, tmp_dir)
        from app.services import app_service

        state = app_service.get_state(
            temp_db,
            user={"username": "alice", "role": "RM", "display_name": "Alice"},
        )
        app = next(a for a in state["apps"] if a["id"] == result["app_id"])
        assert app["cicd_onboarding_status"] == "rejected_create"
        assert app["cicd_onboarding_review_note"] == "镜像配置不符合要求"

    def test_rejected_identity_retry_reuses_app(self, temp_db, tmp_dir):
        result, _ = self._submit_and_reject(temp_db, tmp_dir)
        before_count = temp_db.execute("SELECT COUNT(*) FROM apps").fetchone()[0]

        retry = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="owner2",
            submitter_role="Owner",
            payload=_BUILD_PAYLOAD,
        )

        after_count = temp_db.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        assert after_count == before_count
        assert retry["action"] == "associated"
        assert retry["app_id"] == result["app_id"]
        assert _payload(retry["request"])["app_name"] == _OFFICIAL_NAME
        pending = temp_db.execute(
            "SELECT COUNT(*) FROM cicd_task_requests WHERE status='pending' AND request_type='create'"
        ).fetchone()[0]
        assert pending == 1

    def test_rejected_identity_retry_with_different_name_is_rejected(self, temp_db, tmp_dir):
        result, _ = self._submit_and_reject(temp_db, tmp_dir)

        with pytest.raises(RuntimeError, match="不能用新名称重复创建"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="DifferentRetryName",
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="owner2",
                submitter_role="Owner",
                payload={**_BUILD_PAYLOAD, "app_name": "DifferentRetryName"},
            )

        assert _request_count(temp_db) == 1
        assert apps_repo.get_app(temp_db, result["app_id"]) is not None
