"""Frozen release lookup uses that cycle's publication and QA records."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain.container_image import delivered_image_reference
from app.integrations import gerrit, harbor
from app.main import create_app
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, close_signup, make_app_info, seed_app, seed_cycle, seed_signup, seed_legacy_qa

ADDRESS = "harbor.sh.mxcr.io/hpc-release/hpl-maca"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture()
def history(temp_db, monkeypatch):
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: pytest.fail("publication lookup must stay local"))
    monkeypatch.setattr(gerrit, "fetch_app_info_at", lambda *args, **kwargs: pytest.fail("publication lookup must stay local"))
    for app_id in ("passed", "issues", "failed", "withdrawn", "missing"):
        seed_app(temp_db, app_id, official_name=app_id.upper())
    empty = seed_cycle(temp_db, name="empty")
    close_qa(temp_db, empty)
    cycle_service.publish(temp_db, empty, user="rm", role="RM")
    older = seed_cycle(temp_db, name="3.9.0")
    seed_signup(temp_db, older, "passed", target_version="0.9")
    seed_legacy_qa(temp_db, older, [
        {"app_id": "passed", "status": "has_issues", "issue_note": "旧版问题",
         "deliverable_image": ADDRESS + ":old", "deliverable_digest": "b" * 64},
    ], user="qa", role="QA")
    close_qa(temp_db, older)
    cycle_service.publish(temp_db, older, user="rm", role="RM")
    recent = seed_cycle(temp_db, name="3.10.0")
    for app_id in ("passed", "issues", "failed", "withdrawn", "missing"):
        entry = seed_signup(temp_db, recent, app_id, target_version="1.0")
        if app_id == "withdrawn":
            cycle_service.withdraw(temp_db, entry["id"], user="rm", role="RM")
    seed_legacy_qa(temp_db, recent, [
        {"app_id": "passed", "status": "qa_passed", "deliverable_image": ADDRESS, "deliverable_digest": DIGEST},
        {"app_id": "issues", "status": "has_issues", "issue_note": "性能有波动",
         "deliverable_image": f"{ADDRESS}@{DIGEST}"},
        {"app_id": "failed", "status": "cannot_release", "issue_note": "运行失败"},
        {"app_id": "missing", "status": "qa_passed"},
    ], user="qa", role="QA")
    close_qa(temp_db, recent)
    cycle_service.publish(temp_db, recent, user="rm", role="RM")
    open_cycle = seed_cycle(temp_db, name="3.11")
    seed_signup(temp_db, open_cycle, "passed", target_version="2.0")
    close_signup(temp_db, open_cycle)
    return {"empty": empty, "older": older, "recent": recent, "open": open_cycle}


def test_lists_only_locked_cycles_newest_first_and_handles_empty_history(temp_db, history):
    audit_before = temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    result = lookup_service.published_releases(temp_db)
    assert [release["id"] for release in result] == [history[key] for key in ("recent", "older", "empty")]
    assert lookup_service.published_apps(temp_db, history["empty"])["apps"] == []
    assert temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit_before


def test_lists_successful_entries_with_exact_cycle_versions_qa_and_complete_images(temp_db, history):
    # A stopped App and a newer app_info must not erase or rewrite publication history.
    temp_db.execute("UPDATE apps SET cicd_status='stopped' WHERE id='issues'")
    temp_db.commit()
    catalog_service.store_app_info(temp_db, "passed", make_app_info(app_version="9.0"),
                                   commit_id="new-head", source="test", user="rm")
    result = lookup_service.published_apps(temp_db, history["recent"])
    assert result["release"]["name"] == "3.10.0"
    rows = result["apps"]
    assert [row["app_id"] for row in rows] == ["issues", "missing", "passed"]
    assert {row["app_version"] for row in rows} == {"1.0"}
    assert rows[0]["qa_status"] == "has_issues"
    assert rows[0]["qa_note"] == "性能有波动"
    assert rows[0]["image_reference"] == f"{ADDRESS}@{DIGEST}"
    assert rows[1]["image_reference"] == ""
    assert rows[2]["qa_status"] == "qa_passed"
    assert rows[2]["image_reference"] == f"{ADDRESS}@{DIGEST}"
    old = lookup_service.published_apps(temp_db, history["older"])["apps"][0]
    assert old["app_version"] == "0.9"
    assert old["qa_status"] == "has_issues"
    assert old["qa_note"] == "旧版问题"
    assert old["image_reference"] == f"{ADDRESS}:old@sha256:{'b' * 64}"


def test_unlock_removes_version_and_republish_updates_its_results(temp_db, history):
    release_id = history["recent"]
    cycle_service.unlock(temp_db, release_id, reason="更正 QA", user="rm", role="RM")
    assert release_id not in [row["id"] for row in lookup_service.published_releases(temp_db)]
    with pytest.raises(ValueError, match="解锁"):
        lookup_service.published_apps(temp_db, release_id)
    qa_service.set_qa_status_batch(temp_db, release_id, [
        {"app_id": "passed", "status": "cannot_release", "issue_note": "更正为不可发布"},
    ], user="qa", role="QA")
    cycle_service.publish(temp_db, release_id, user="rm", role="RM")
    assert "passed" not in [row["app_id"] for row in lookup_service.published_apps(temp_db, release_id)["apps"]]


@pytest.mark.parametrize("role,status", [("Owner", 200), ("QA", 200), ("RM", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_read_api_contract_and_role_boundaries(temp_db, history, role, status):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app, raise_server_exceptions=False) as client:
        options = client.get("/api/lookup/releases")
        result = client.get("/api/lookup/release", params={"release_id": history["recent"]})
        assert options.status_code == result.status_code == status
        if status == 200:
            assert len(options.json()["releases"]) == 3
            assert len(result.json()["apps"]) == 3
            assert client.get("/api/lookup/release", params={"release_id": history["open"]}).status_code == 400
            unknown = client.get("/api/lookup/release", params={"release_id": "unknown"})
            assert unknown.status_code == 400
            assert "版本不存在" in unknown.json()["error"]


@pytest.mark.parametrize("image,digest,expected", [
    (ADDRESS, DIGEST, f"{ADDRESS}@{DIGEST}"),
    (f"docker pull {ADDRESS}:tag", DIGEST, f"{ADDRESS}:tag@{DIGEST}"),
    (f"{ADDRESS}@{DIGEST}", "", f"{ADDRESS}@{DIGEST}"),
    (f"{ADDRESS}@sha256:{'b' * 64}", DIGEST, f"{ADDRESS}@{DIGEST}"),
    (ADDRESS, "A" * 64, f"{ADDRESS}@{DIGEST}"),
    (ADDRESS, "", ""), ("", DIGEST, ""), (ADDRESS, "sha256:invalid", ""),
    (f"{ADDRESS}\n{ADDRESS}", DIGEST, ""),
])
def test_delivery_reference_composition_and_incomplete_legacy_fields(image, digest, expected):
    assert delivered_image_reference(image, digest) == expected
