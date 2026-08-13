"""CICD-first app creation after the app-backed cutover.

The active model has no generated CICD task id.  `apps.id` is the canonical
identity, and `cicd_task_requests.task_id` is stored with the same app id for
the existing API field.
"""
from __future__ import annotations

import json

import pytest

from app.integrations import jira as jira_integration
from app.repositories import apps_repo
from app.services import cicd_service
from app.timeutil import beijing_timestamp
from release_system import core
from tests.conftest import seed_release

_REPO_SHORT = "hpc_w3cicd"
_BRANCH = "wave3"
_OFFICIAL_NAME = "W3CicdFirst"
_APP_ID = "w3cicdfirst"
_RESOLVED_URL = "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_w3cicd"

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

    def test_git_create_requires_short_hpc_path(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="短路径"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="FullUrlApp",
                repo_type="git",
                repo_name=_RESOLVED_URL,
                branch="main",
                submitter="rm",
                submitter_role="RM",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0
        assert _task_table_count(temp_db) == 0

    def test_repo_create_requires_xml_path(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="XML 路径"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name="ManifestApp",
                repo_type="repo",
                repo_name="APP/openfoam",
                branch="master",
                submitter="rm",
                submitter_role="RM",
                payload={**_BUILD_PAYLOAD, "cicd_repo_type": "repo"},
            )
        assert _request_count(temp_db) == 0
        assert _task_table_count(temp_db) == 0


class TestCicdFirstAppBackedLifecycle:
    def test_app_form_cicd_fields_reach_request_detail_and_jira(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="owner",
            submitter_role="Owner",
            payload={
                "cicd_repo_type": "git",
                "cicd_community_artifact": "image, pkg",
                "cicd_build_image": "pyg",
                "cicd_test_timeout": "55",
                "cicd_notes": "依赖 pyg",
            },
        )

        payload = _payload(result["request"])
        assert payload["community_artifact"] == ["image", "pkg"]
        assert payload["build_image"] == "pyg"
        assert payload["test_timeout"] == 55
        assert payload["notes"] == "依赖 pyg"
        app = apps_repo.get_app(temp_db, result["app_id"])
        assert app is not None
        assert app["cicd_community_artifact"] == "image, pkg"
        assert app["cicd_build_image"] == "pyg"
        assert app["cicd_test_timeout"] == "55"
        assert app["cicd_notes"] == "依赖 pyg"

        description = jira_integration.build_description(
            request_id=result["request"]["id"],
            request_type="create",
            payload=payload,
            task_id=result["app_id"],
            submitter="owner",
            title="[New] W3CicdFirst 【新发布项目】",
        )
        assert "|构建依赖镜像|pyg|" in description
        assert "|备注|依赖 pyg|" in description

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
        assert _payload(req)["release_decision"] == "cicd_only"
        assert _task_table_count(temp_db) == 0

        snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
        assert snap["release_decision"] == "cicd_only"
        assert snap["owners"] == ["rm"]

        task = next(item for item in cicd_service.list_tasks(temp_db) if item["app_id"] == _APP_ID)
        assert task["status"] == "Running"
        assert task["cicd_onboarding_status"] == "pending_create"

    def test_release_decision_is_committed_at_submission_and_survives_late_delivery(
        self,
        temp_db,
        tmp_dir,
    ):
        from app.services import qa_service

        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        temp_db.execute(
            "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
            ("2099-12-30 23:59:59", "2099-12-31 23:59:59", release_id),
        )
        temp_db.commit()
        parsed = {
            "app_name": _OFFICIAL_NAME,
            "app_version": "1.2.3",
            "x86_chips": ["c500"],
            "arm_chips": [],
            "python_labels": [],
            "pytorch_labels": [],
            "build_os": ["ubuntu22.04"],
            "build_arches": ["amd64"],
            "raw": {
                "app_version": "1.2.3",
                "app_build": {
                    "ubuntu22.04_amd64": {
                        "build_target": "release",
                        "arch": "amd64",
                        "docker_image": "hpc/w3cicd:1.2.3",
                    }
                },
                "app_test": {
                    "sanity": {
                        "test_cmd": "w3cicd --version",
                        "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
                    }
                },
            },
        }

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            release_id=release_id,
            release_decision="release",
            payload=_BUILD_PAYLOAD,
            app_info_parsed=parsed,
        )

        snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
        assert snap["release_decision"] == "release"
        assert _payload(result["request"])["release_decision"] == "release"
        report = qa_service.get_qa_reports(temp_db, release_id)
        release_rows = [
            dict(zip(report["release_report"]["columns"], row))
            for row in report["release_report"]["rows"]
        ]
        release_row = next(row for row in release_rows if row["名称"] == _OFFICIAL_NAME)
        test_row = dict(zip(report["test_cmd"]["columns"], report["test_cmd"]["rows"][0]))
        assert release_row["CICD状态"] == "CICD待完成"
        assert test_row["cicd_status"] == "CICD待完成"
        pending_meta = [
            meta
            for row, meta in zip(report["release_report"]["rows"], report["release_report"]["rows_meta"])
            if dict(zip(report["release_report"]["columns"], row))["名称"] == _OFFICIAL_NAME
        ][0]
        assert pending_meta["cicd_pending"] is True

        cicd_service.approve_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-LATE",
        )
        temp_db.execute(
            "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
            ("2000-01-01 00:00:00", "2000-01-02 00:00:00", release_id),
        )
        temp_db.commit()
        cicd_service.deliver_request(
            temp_db,
            result["request"]["id"],
            deliverer="spd",
            deliverer_role="SPD",
        )

        snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
        assert snap["release_decision"] == "release"
        delivered_report = qa_service.get_qa_reports(temp_db, release_id)
        delivered_rows = [
            dict(zip(delivered_report["release_report"]["columns"], row))
            for row in delivered_report["release_report"]["rows"]
        ]
        delivered_row = next(row for row in delivered_rows if row["名称"] == _OFFICIAL_NAME)
        assert delivered_row["CICD状态"] == ""

    def test_release_decision_is_gated_per_target_release_at_submission(self, temp_db, tmp_dir):
        from app.services import release_service

        release_37 = seed_release(temp_db, tmp_path=tmp_dir)
        temp_db.execute(
            "UPDATE releases SET name=?, app_freeze_deadline=?, doc_deadline=? WHERE id=?",
            ("3.7.0", "2099-12-30 23:59:59", "2099-12-31 23:59:59", release_37),
        )
        temp_db.commit()
        release_38 = release_service.create_release(
            temp_db,
            name="3.8.0",
            maca_version="",
            app_freeze_deadline="2000-01-01",
            doc_deadline="2000-01-02",
            user="rm",
            role="RM",
        )["release_id"]
        release_39 = release_service.create_release(
            temp_db,
            name="3.9.0",
            maca_version="",
            app_freeze_deadline="2000-01-01",
            doc_deadline="2099-12-31",
            user="rm",
            role="RM",
        )["release_id"]
        release_310 = release_service.create_release(
            temp_db,
            name="3.10.0",
            maca_version="",
            app_freeze_deadline="2099-12-30",
            doc_deadline="2099-12-31",
            user="rm",
            role="RM",
        )["release_id"]
        temp_db.executemany(
            "UPDATE releases SET created_at=? WHERE id=?",
            [
                ("2026-01-01 00:00:01", release_37),
                ("2026-01-01 00:00:02", release_38),
                ("2026-01-01 00:00:03", release_39),
                ("2026-01-01 00:00:04", release_310),
            ],
        )
        temp_db.commit()

        preview = cicd_service.preview_cicd_first_release_decisions(
            temp_db,
            release_id=release_37,
            release_decision="release",
        )
        preview_by_release = {
            row["release_id"]: row for row in preview["releases"]
        }
        assert preview["scope"] == "current_and_later"
        assert preview_by_release[release_37]["resulting_decision"] == "release"
        assert preview_by_release[release_37]["is_current"] is True
        assert preview_by_release[release_38]["resulting_decision"] == "cicd_only"
        assert preview_by_release[release_39]["resulting_decision"] == "cicd_only"
        assert preview_by_release[release_310]["resulting_decision"] == "release"

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            release_id=release_37,
            release_decision="release",
            payload=_BUILD_PAYLOAD,
        )

        expected = {
            release_37: "release",
            release_38: "cicd_only",
            release_39: "cicd_only",
            release_310: "release",
        }
        for release_id, decision in expected.items():
            snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
            assert snap["release_decision"] == decision

        public_payload = _payload(result["request"])
        assert "_cicd_first_release_decisions" not in public_payload
        stored_payload = json.loads(
            temp_db.execute(
                "SELECT payload FROM cicd_task_requests WHERE id=?",
                (result["request"]["id"],),
            ).fetchone()[0]
        )
        assert stored_payload["_cicd_first_release_decisions"] == expected

        cicd_service.approve_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-PER-RELEASE",
        )
        temp_db.execute(
            "UPDATE releases SET app_freeze_deadline=?, doc_deadline=?",
            ("2000-01-01 00:00:00", "2000-01-02 00:00:00"),
        )
        temp_db.commit()
        cicd_service.deliver_request(
            temp_db,
            result["request"]["id"],
            deliverer="spd",
            deliverer_role="SPD",
        )

        for release_id, decision in expected.items():
            snap = core.get_release(temp_db, release_id)["snapshots"][_APP_ID]
            assert snap["release_decision"] == decision

    def test_release_decision_is_rejected_when_submitted_after_freeze(self, temp_db, tmp_dir):
        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        temp_db.execute(
            "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
            ("2000-01-01 00:00:00", "2000-01-02 00:00:00", release_id),
        )
        temp_db.commit()
        with pytest.raises(RuntimeError, match="已过 app 冻结 deadline"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                release_id=release_id,
                release_decision="release",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0

    def test_release_decision_requires_release_id_for_submission_phase_check(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="必须提供 release_id"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                release_decision="release",
                payload=_BUILD_PAYLOAD,
            )
        assert _request_count(temp_db) == 0

    def test_pending_release_without_app_info_has_test_command_planning_row(self, temp_db, tmp_dir):
        from app.services import qa_service

        release_id = seed_release(temp_db, tmp_path=tmp_dir)
        temp_db.execute(
            "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
            ("2099-12-30 23:59:59", "2099-12-31 23:59:59", release_id),
        )
        temp_db.commit()
        cicd_service.cicd_first_new_app(
            temp_db,
            official_name=_OFFICIAL_NAME,
            repo_type="git",
            repo_name=_REPO_SHORT,
            branch=_BRANCH,
            submitter="rm",
            submitter_role="RM",
            release_id=release_id,
            release_decision="release",
            payload=_BUILD_PAYLOAD,
        )

        report = qa_service.get_qa_reports(temp_db, release_id)
        rows = [
            dict(zip(report["test_cmd"]["columns"], row))
            for row in report["test_cmd"]["rows"]
        ]
        planning_row = next(row for row in rows if row["app_name"] == _OFFICIAL_NAME)
        assert planning_row["docker_cmd"] == ""
        assert planning_row["cicd_status"] == "CICD待完成"

    def test_stopped_is_not_a_cicd_first_target_decision(self, temp_db, tmp_dir):
        seed_release(temp_db, tmp_path=tmp_dir)
        with pytest.raises(ValueError, match="release 或 cicd_only"):
            cicd_service.cicd_first_new_app(
                temp_db,
                official_name=_OFFICIAL_NAME,
                repo_type="git",
                repo_name=_REPO_SHORT,
                branch=_BRANCH,
                submitter="rm",
                submitter_role="RM",
                release_decision="stopped",
                payload=_BUILD_PAYLOAD,
            )

    def test_repo_create_persists_manifest_config_not_resolved_identity(
        self,
        temp_db,
        tmp_dir,
        monkeypatch,
    ):
        manifest_path = "APP/slurm/hpc_slurm_22.05.3.xml"
        resolved_url = "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_slurm"
        resolution_calls = []

        def fake_identity(repo_type, repo_name, branch, **kwargs):
            resolution_calls.append((repo_type, repo_name, branch, kwargs))
            return resolved_url, "dev"

        monkeypatch.setattr("app.identity.repo_to_git_identity", fake_identity)
        seed_release(temp_db, tmp_path=tmp_dir)

        result = cicd_service.cicd_first_new_app(
            temp_db,
            official_name="Slurm",
            repo_type="repo",
            repo_name=manifest_path,
            branch="master",
            submitter="rm",
            submitter_role="RM",
            payload={**_BUILD_PAYLOAD, "cicd_repo_type": "repo"},
        )

        app = apps_repo.get_app(temp_db, result["app_id"])
        assert app["git_url"] == manifest_path
        assert app["git_branch"] == "master"
        assert result["git_url"] == resolved_url
        assert result["git_branch"] == "dev"
        assert resolution_calls == [
            (
                "repo",
                manifest_path,
                "master",
                {"refresh_manifest": True},
            )
        ]

        cicd_service.approve_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
        )
        app = apps_repo.get_app(temp_db, result["app_id"])
        assert app["git_url"] == manifest_path
        assert app["git_branch"] == "master"

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

    def test_dispatch_approval_keeps_owner_decision_visible_before_delivery(self, temp_db, tmp_dir):
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
        assert snap["release_decision"] == "cicd_only"

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

    def test_cancel_rolls_owner_decision_back_to_stopped(self, temp_db, tmp_dir):
        result = _create_app(temp_db, tmp_dir)
        before = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"]
        assert before[result["app_id"]]["release_decision"] == "cicd_only"

        cancelled = cicd_service.cancel_request(
            temp_db,
            result["request"]["id"],
            username="rm",
            role="RM",
        )

        assert cancelled["status"] == "cancelled"
        after = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"]
        assert after[result["app_id"]]["release_decision"] == "stopped"

    def test_reject_returned_delivery_rolls_owner_decision_back_to_stopped(
        self,
        temp_db,
        tmp_dir,
    ):
        result = _create_app(temp_db, tmp_dir)
        cicd_service.approve_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
            approval_mode="dispatch_spd",
            jira_id="HPC-RETURN",
        )
        cicd_service.return_delivery(
            temp_db,
            result["request"]["id"],
            returner="spd",
            returner_role="SPD",
            reason="构建配置需调整",
        )
        rejected = cicd_service.reject_returned_request(
            temp_db,
            result["request"]["id"],
            reviewer="rm",
            reviewer_role="RM",
            review_note="本轮不再交付",
        )

        assert rejected["status"] == "rejected"
        snap = core.get_release(temp_db, core.list_releases(temp_db)[0]["id"])["snapshots"]
        assert snap[result["app_id"]]["release_decision"] == "stopped"

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
