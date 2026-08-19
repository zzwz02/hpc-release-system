from __future__ import annotations

import pytest

from app.config import settings
from app.domain import qa
from app.domain.snapshots import normalize_doc_target
from app.services import artifact_service, qa_service, release_service
from release_system import core
from tests.conftest import seed_app, seed_snapshot


def test_qa_cannot_release_requires_issue_note(release_with_snapshot):
    conn, release_id, app_id = release_with_snapshot

    with pytest.raises(ValueError, match="不可发布.*问题说明"):
        qa_service.set_qa_status_batch(
            conn,
            release_id,
            [{"app_id": app_id, "status": "cannot_release", "issue_note": ""}],
            user="qa",
            role="QA",
        )

    snap = core.get_release(conn, release_id)["snapshots"][app_id]
    assert snap["qa_status"] == "not_checked"
    assert snap["qa_issue_note"] == ""


def test_qa_cannot_release_note_is_saved_and_rendered_in_draft_artifacts(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)

    qa_service.set_qa_status_batch(
        conn,
        release_id,
        [{"app_id": app_id, "status": "cannot_release", "issue_note": "C500 阻塞发布"}],
        user="qa",
        role="QA",
    )

    snap = core.get_release(conn, release_id)["snapshots"][app_id]
    assert snap["qa_status"] == "cannot_release"
    assert snap["qa_issue_note"] == "C500 阻塞发布"

    artifacts = artifact_service.generate_artifacts(
        conn,
        release_id,
        user="rm",
        role="RM",
    )
    assert "QA状态" in artifacts["release_note"]
    assert "QA问题说明" in artifacts["release_note"]
    assert "不可发布" in artifacts["release_note"]
    assert "C500 阻塞发布" in artifacts["release_note"]
    assert "QA 不可发布：C500 阻塞发布" in artifacts["manual"]


def test_qa_report_blanks_legacy_community_fields_without_cicd_artifact(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)

    def _legacy_community(snapshot: dict) -> None:
        snapshot["community"] = {
            "release_status": "未发布",
            "python_version": "无",
            "framework_version": "无",
        }

    core.update_snapshot(conn, release_id, app_id, _legacy_community)

    report = qa_service.get_qa_reports(conn, release_id)["release_report"]
    row = dict(zip(report["columns"], report["rows"][0]))

    assert row["开发者社区发布情况"] == ""
    assert row["开发者社区发布包支持python版本"] == ""
    assert row["开发者社区发布包支持的底层框架及版本"] == ""


def test_qa_report_unknown_doc_target_falls_back_to_manual(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)
    core.update_snapshot(
        conn,
        release_id,
        app_id,
        lambda snapshot: snapshot.update({"doc_target": "unexpected-target"}),
    )

    report = qa_service.get_qa_reports(conn, release_id)["release_report"]
    row = dict(zip(report["columns"], report["rows"][0]))

    assert normalize_doc_target("unexpected-target") == "manual"
    assert row["类别"] == "HPC"


def test_shared_qa_status_behavior():
    assert qa.QA_STATUS_DEFAULT == "not_checked"
    assert qa.issue_note_required("has_issues")
    assert qa.issue_note_required("cannot_release")
    assert qa.is_releasable("qa_passed")
    assert qa.is_releasable("has_issues")
    assert not qa.is_releasable("cannot_release")


def test_qa_report_keeps_community_fields_when_cicd_artifact_is_selected(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)
    conn.execute("UPDATE apps SET cicd_community_artifact = 'image' WHERE id = ?", (app_id,))

    def _community(snapshot: dict) -> None:
        snapshot["community"] = {
            "release_status": "已发布",
            "python_version": "Python 3.10",
            "framework_version": "PyTorch 2.1",
        }

    core.update_snapshot(conn, release_id, app_id, _community)

    report = qa_service.get_qa_reports(conn, release_id)["release_report"]
    row = dict(zip(report["columns"], report["rows"][0]))

    assert row["开发者社区发布情况"] == "已发布"
    assert row["开发者社区发布包支持python版本"] == "Python 3.10"
    assert row["开发者社区发布包支持的底层框架及版本"] == "PyTorch 2.1"


def test_qa_report_hides_non_release_apps_without_compare(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)

    def _stop_release(snapshot: dict) -> None:
        snapshot["release_decision"] = "stopped"

    core.update_snapshot(conn, release_id, app_id, _stop_release)

    report = qa_service.get_qa_reports(conn, release_id)["release_report"]

    assert report["rows"] == []
    assert report["rows_meta"] == []


def test_qa_report_keeps_changed_non_release_app_but_blanks_release_fields(release_with_app):
    conn, base_release_id, app_id = release_with_app
    seed_snapshot(conn, base_release_id, app_id, owner_confirmed=True)
    current_release_id = core.create_release_from_previous(conn, "next")
    conn.execute("UPDATE apps SET cicd_community_artifact = 'image' WHERE id = ?", (app_id,))

    def _stop_with_release_fields(snapshot: dict) -> None:
        snapshot["release_decision"] = "stopped"
        snapshot["x86_chips"] = "c500,x301"
        snapshot["arm_chips"] = "x201"
        snapshot["community"] = {
            "release_status": "已发布",
            "python_version": "Python 3.10",
            "framework_version": "PyTorch 2.1",
        }
        snapshot["sanity"] = {"arm_kylin": True, "ubuntu": True}

    core.update_snapshot(conn, current_release_id, app_id, _stop_with_release_fields)

    report = qa_service.get_qa_reports(
        conn,
        current_release_id,
        compare_release_id=base_release_id,
    )["release_report"]
    row = dict(zip(report["columns"], report["rows"][0]))

    assert len(report["rows"]) == 1
    assert report["rows_meta"] == [{"release_decision": "stopped", "is_release": False}]
    assert row["名称"] == "TestApp"
    assert "停止发布" in row["对比"]
    for column in [
        "X86支持芯片系列",
        "ARM支持芯片类型",
        "开发者社区发布情况",
        "开发者社区发布包支持python版本",
        "开发者社区发布包支持的底层框架及版本",
        "ARM / Kylin sanity",
        "Ubuntu / 兼容性 sanity",
    ]:
        assert row[column] == ""


@pytest.mark.parametrize(
    ("base_decision", "current_decision"),
    [
        ("cicd_only", "cicd_only"),
        ("cicd_only", "stopped"),
        ("stopped", "cicd_only"),
        ("stopped", "stopped"),
    ],
)
def test_qa_report_compare_hides_changes_wholly_outside_qa_scope(
    release_with_app,
    base_decision,
    current_decision,
):
    conn, base_release_id, app_id = release_with_app
    seed_snapshot(
        conn,
        base_release_id,
        app_id,
        app_info={
            "app_version": "1.0",
            "app_test": {"old": {"enabled": True, "test_cmd": "run-old"}},
        },
    )
    core.update_snapshot(
        conn,
        base_release_id,
        app_id,
        lambda snapshot: snapshot.update({"release_decision": base_decision}),
    )
    current_release_id = core.create_release_from_previous(conn, "next")

    def _change_non_qa_snapshot(snapshot: dict) -> None:
        snapshot["release_decision"] = current_decision
        snapshot["version"] = "2.0"
        snapshot["test_docs"] = [{"path": "new-test.md", "content": "changed"}]
        snapshot["app_info"] = {
            "raw": {
                "app_version": "2.0",
                "app_test": {"new": {"enabled": True, "test_cmd": "run-new"}},
            }
        }

    core.update_snapshot(conn, current_release_id, app_id, _change_non_qa_snapshot)

    report = qa_service.get_qa_reports(
        conn,
        current_release_id,
        compare_release_id=base_release_id,
    )["release_report"]

    assert report["rows"] == []
    assert report["rows_meta"] == []


def test_qa_test_cmd_only_filters_explicitly_disabled_tests(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(
        conn,
        release_id,
        app_id,
        app_info={
            "app_version": "2023.0807",
            "app_name": "hpl",
            "app_build": {
                "ubuntu22.04_amd64": {
                    "build_target": "release",
                    "arch": "amd64",
                    "supported_chip": ["c500"],
                },
            },
            "app_test": {
                "default_enabled": {
                    "test_cmd": "/opt/rocHPL/run_hpl_autotest.sh",
                    "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
                    "perf_golden": "max",
                    "perf_unit": "GFlops",
                },
                "minimum_enabled": {
                    "test_cmd": "/opt/rocHPL/run_latency_test.sh",
                    "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
                    "perf_golden": "min",
                    "perf_unit": "ms",
                },
                "explicitly_disabled": {
                    "test_cmd": "/opt/rocHPL/run_disabled_test.sh",
                    "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
                    "enabled": False,
                },
            },
        },
    )

    report = qa_service.get_qa_reports(conn, release_id)["test_cmd"]

    rows = {
        row["test_name"]: row
        for values in report["rows"]
        for row in [dict(zip(report["columns"], values))]
    }
    assert set(rows) == {"default_enabled", "minimum_enabled"}
    assert rows["default_enabled"]["docker_cmd"].endswith(
        "sh -c '/opt/rocHPL/run_hpl_autotest.sh'"
    )
    assert rows["default_enabled"]["perf_golden"] == "max"
    assert rows["default_enabled"]["perf_unit"] == "GFlops"
    assert rows["minimum_enabled"]["perf_golden"] == "min"
    assert rows["minimum_enabled"]["perf_unit"] == "ms"


def test_qa_test_cmd_leaves_missing_perf_fields_blank(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id)

    report = qa_service.get_qa_reports(conn, release_id)["test_cmd"]
    row = dict(zip(report["columns"], report["rows"][0]))

    assert row["perf_golden"] == ""
    assert row["perf_unit"] == ""


def test_release_note_includes_release_app_even_when_qa_is_not_passed(release_with_app):
    conn, release_id, _app_id = release_with_app
    deepmd_id = seed_app(
        conn,
        release_id,
        official_name="DeepMD",
        git_url=f"{settings.gerrit_hpc_base_url}/hpc_deepmd",
        git_branch="maca",
        release_decision="release",
        owner="deepmd_owner",
    )
    seed_snapshot(
        conn,
        release_id,
        deepmd_id,
        app_info={
            "app_version": "3.0",
            "app_name": "deepmd",
            "app_build": {
                "ubuntu20.04_amd64": {
                    "build_target": "release",
                    "arch": "amd64",
                    "supported_chip": ["c500"],
                    "enabled": True,
                },
            },
            "app_test": {
                "sanity": {
                    "test_cmd": "deepmd --version",
                    "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
                    "enabled": True,
                },
            },
        },
        owner_confirmed=True,
    )

    def _complete_deepmd_metadata(snapshot: dict) -> None:
        snapshot["type"] = "分子动力学"

    core.update_snapshot(conn, release_id, deepmd_id, _complete_deepmd_metadata)

    artifacts = artifact_service.generate_artifacts(
        conn,
        release_id,
        user="rm",
        role="RM",
    )

    assert "DeepMD 3.0" in artifacts["release_note"]
    assert "未测试" in artifacts["release_note"]


def test_final_lock_uses_cannot_release_note_in_release_note(release_with_app):
    conn, release_id, app_id = release_with_app
    seed_snapshot(conn, release_id, app_id, owner_confirmed=True)
    qa_service.set_qa_status_batch(
        conn,
        release_id,
        [{"app_id": app_id, "status": "cannot_release", "issue_note": "X201 全量失败"}],
        user="qa",
        role="QA",
    )

    result = release_service.final_lock(
        conn,
        release_id=release_id,
        user="rm",
        role="RM",
    )
    assert "release_note" in result["artifacts"]

    row = conn.execute(
        "SELECT content, final FROM artifacts WHERE release_id = ? AND kind = 'release_note'",
        (release_id,),
    ).fetchone()
    assert row["final"] == 1
    assert "不可发布" in row["content"]
    assert "X201 全量失败" in row["content"]
