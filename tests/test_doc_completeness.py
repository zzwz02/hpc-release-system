from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain import gates
from app.main import create_app
from app.repositories import snapshots_repo
from app.services import app_service, artifact_service, qa_service


def minimal_snapshot(**overrides):
    snapshot = {
        "release_decision": "release",
        "doc_target": "manual",
        "type": "蛋白质与生物分子模型",
        "owner_confirmed": False,
        "qa_status": "not_checked",
    }
    snapshot.update(overrides)
    return snapshot


@pytest.mark.parametrize("target", ["manual", "ai4sci", "HPC", "AI4Sci"])
def test_only_classification_is_required_without_community_artifacts(target):
    snapshot = minimal_snapshot(
        doc_target=target, description="长" * 40,
        doc={}, test_docs=[{"path": "test", "owner_added": True}],
    )
    snapshot["missing_items"] = gates.missing_items_for({}, snapshot)
    assert gates.docs_gate_items(snapshot) == []
    assert gates.qualifies_for_docs(snapshot)
    assert not gates.qualifies_for_final(snapshot)
    assert gates.not_releasable_reason(snapshot) == "QA未测试"


@pytest.mark.parametrize("target", [None, "", "  ", "unknown"])
def test_doc_target_must_be_an_explicit_hpc_or_ai4sci_selection(target):
    snapshot = minimal_snapshot(doc_target=target, type=" \t ")
    snapshot["missing_items"] = gates.missing_items_for({}, snapshot)
    assert [item["text"] for item in gates.docs_gate_items(snapshot)] == [
        "缺少类型（HPC/AI4Sci）", "缺少 App类型",
    ]
    assert not gates.qualifies_for_docs(snapshot)


@pytest.mark.parametrize("artifacts", ["image", "pkg", "image, pkg", "镜像，软件包", ["package"]])
def test_community_artifacts_require_exactly_three_nonblank_fields(artifacts):
    app = {"cicd_community_artifact": artifacts}
    snapshot = minimal_snapshot(community={
        "release_status": " \t ", "python_version": "", "framework_version": None,
    })
    snapshot["missing_items"] = gates.missing_items_for(app, snapshot)
    assert [item["text"] for item in gates.docs_gate_items(snapshot)] == [
        "缺少开发者社区发布情况", "缺少社区包支持 Python 版本", "缺少社区包支持框架及版本",
    ]
    assert not gates.qualifies_for_docs(snapshot)
    snapshot["community"] = {
        "release_status": "未发布", "python_version": "无", "framework_version": "无",
    }
    snapshot["missing_items"] = gates.missing_items_for(app, snapshot)
    assert gates.qualifies_for_docs(snapshot)


@pytest.mark.parametrize("artifacts", [None, "", "  ", "unknown"])
def test_unselected_artifacts_do_not_require_community_fields(artifacts):
    snapshot = minimal_snapshot()
    snapshot["missing_items"] = gates.missing_items_for({"cicd_community_artifact": artifacts}, snapshot)
    assert gates.docs_gate_items(snapshot) == []


@pytest.mark.parametrize("status,releasable", [
    ("not_checked", False), ("qa_passed", True), ("has_issues", True), ("cannot_release", False),
])
def test_owner_confirmation_is_not_a_docs_or_qa_release_gate(status, releasable):
    snapshot = minimal_snapshot(qa_status=status)
    snapshot["missing_items"] = gates.missing_items_for({}, snapshot)
    assert gates.qualifies_for_docs(snapshot)
    assert gates.qualifies_for_final(snapshot) is releasable
    assert "Owner" not in gates.not_releasable_reason(snapshot)


@pytest.mark.parametrize("decision", ["cicd_only", "stopped"])
def test_nonrelease_apps_remain_excluded(decision):
    snapshot = minimal_snapshot(release_decision=decision, doc_target="", type="")
    assert gates.missing_items_for({"cicd_community_artifact": "image"}, snapshot) == []
    assert not gates.qualifies_for_docs(snapshot)
    assert not gates.qualifies_for_final(snapshot)


@pytest.mark.parametrize("artifacts", ["image", "pkg", "image, pkg"])
def test_community_fields_control_artifact_inclusion_without_owner_confirmation(release_with_app, artifacts):
    conn, release_id, app_id = release_with_app
    conn.execute("UPDATE apps SET cicd_community_artifact = ? WHERE id = ?", (artifacts, app_id))
    conn.commit()
    draft = artifact_service.generate_artifacts(conn, release_id, user="rm", role="RM")
    assert "TestApp" not in draft["release_note"]
    result = app_service.update_snapshot(
        conn, release_id, app_id, user="test_owner", role="Owner",
        fields={"snapshot": {"community": {
            "release_status": "已发布", "python_version": "Python 3.10", "framework_version": "无",
        }}},
    )
    assert not result["snapshot"]["owner_confirmed"]
    assert gates.docs_gate_items(result["snapshot"]) == []
    draft = artifact_service.generate_artifacts(conn, release_id, user="rm", role="RM")
    assert "TestApp" in draft["release_note"]
    assert "TestApp" in draft["manual"]


@pytest.mark.parametrize("target", ["manual", "ai4sci"])
def test_owner_save_and_artifacts_do_not_require_doc_confirmation(release_with_app, target):
    conn, release_id, app_id = release_with_app
    initial = snapshots_repo.get_snapshot(conn, release_id, app_id)
    initial["doc_target"] = target
    snapshots_repo.save_snapshot(conn, release_id, app_id, initial)
    conn.commit()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: conn
    user = {
        "username": "test_owner", "role": "Owner", "display_name": "Owner",
    }
    app.dependency_overrides[require_login] = lambda: user
    with TestClient(app) as client:
        response = client.post("/api/apps/update", json={
            "release_id": release_id, "app_id": app_id,
            "snapshot": {"doc_target": target, "type": "蛋白质与生物分子模型"},
        })
        assert response.status_code == 200, response.text
        state = client.get("/api/state", params={"release_id": release_id}).json()
        snapshot = state["release"]["snapshots"][app_id]
        assert not snapshot["owner_confirmed"]
        assert not snapshot["app_info"]
        assert gates.docs_gate_items(snapshot) == []
        response = client.post("/api/artifacts/generate", json={"release_id": release_id})
        assert response.status_code == 200, response.text
        assert "TestApp" in client.get("/api/artifacts/release_note", params={"release_id": release_id}).text
        assert "TestApp" in client.get(f"/api/artifacts/{target}", params={"release_id": release_id}).text
        user["username"] = "another_owner"
        response = client.post("/api/apps/update", json={
            "release_id": release_id, "app_id": app_id, "snapshot": {"type": "无权修改"},
        })
        assert response.status_code == 403

    qa_service.set_qa_status_batch(
        conn, release_id, [{"app_id": app_id, "status": "qa_passed"}], user="qa", role="QA",
    )
    review = artifact_service.generate_manager_review(conn, release_id, user="rm", role="RM")
    row = next(csv.DictReader(io.StringIO(review)))
    assert row["是否可发布"] == "是"
    assert row["不可发布原因"] == ""
    artifact_service.final_lock_release(conn, release_id, user="rm", role="RM")
    snapshot = snapshots_repo.get_snapshot(conn, release_id, app_id)
    assert snapshot["locked_in_release"] is True
    assert not snapshot["owner_confirmed"]
