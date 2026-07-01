from __future__ import annotations

import pytest

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


def test_release_note_includes_release_app_even_when_qa_is_not_passed(release_with_app):
    conn, release_id, _app_id = release_with_app
    deepmd_id = seed_app(
        conn,
        release_id,
        official_name="DeepMD",
        git_url="ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_deepmd",
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
