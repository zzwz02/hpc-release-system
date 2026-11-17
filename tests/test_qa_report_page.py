"""QA report combines its state, saved failures, tag projections and audit in one read."""
import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import qa_results
from app.main import create_app
from app.repositories import entries_repo, qa_results_repo
from app.services import qa_report_service, qa_results_service, qa_service
from tests.test_delivery_images import delivery as delivery
from tests.test_qa_delivery_display import legacy_images


@pytest.fixture()
def prepared(temp_db, delivery):
    stored = legacy_images(temp_db, delivery)
    row = {**dict.fromkeys(qa_results.WORKSHEET_COLUMNS, ""),
           "app_id": delivery[0], "git_branch": "maca", "app_version": "1.0",
           "hpc_version": "3.10.0.7", "arch": "x86", "OS": "ubuntu22.04", "GPU": "C500",
           "test_name": "sanity", "qa_status": "fail", "qa_comment": "测试失败",
           "qa_env": "server", "qa_digest": stored["deliverable_images"][0]["reference"]}
    qa_results_repo.save(temp_db, filename="report.xlsx", content=b"test-workbook", environments=["server"],
                         uploaded_at="2026-11-16 12:00:00", uploaded_by="qa", release_id=delivery[1],
                         rows=[(qa_results.worksheet_result_key(row), row, row["qa_digest"])])
    temp_db.commit()
    return stored


def test_report_includes_original_edit_values_completed_tags_failures_and_audit(temp_db, delivery, prepared):
    before = temp_db.total_changes
    result = qa_report_service.get_report(temp_db, user={"username": "qa", "role": "QA"}, release_id=delivery[1])
    assert result["release"]["id"] == delivery[1]
    entry = next(item for item in result["release"]["entries"] if item["app_id"] == delivery[0])
    assert entry["deliverable_images"] == prepared["deliverable_images"]
    report = result["qa_report"]
    assert report["errors"] == {}
    assert report["failure_summary"]["total_rows"] == report["failure_summary"]["failed_rows"] == 1
    assert report["failure_summary"]["groups"][0]["failures"][0]["qa_comment"] == "测试失败"
    images = report["delivery_images"]["entries"][delivery[0]]
    assert images["revision"] == entry["delivery_revision"]
    for image, original in zip(images["images"], prepared["deliverable_images"], strict=True):
        assert ":1.0-abcdef1-" in image["reference"]
        assert image["reference"].endswith("@" + original["digest"])
        assert not image.get("reference_error")
    assert result["qa_audit_logs"][delivery[0]]
    assert entries_repo.get_entry(temp_db, prepared["id"]) == prepared
    assert temp_db.total_changes == before


@pytest.mark.parametrize("section", ["failure_summary", "delivery_images"])
def test_section_errors_do_not_hide_other_report_data(temp_db, delivery, prepared, monkeypatch, section):
    def fail(*args, **kwargs):
        raise ApiError(502, "上游读取失败")
    target, method = (qa_results_service, "failure_summary") if section == "failure_summary" else (qa_service, "delivery_images_view")
    monkeypatch.setattr(target, method, fail)
    result = qa_report_service.get_report(temp_db, user={"username": "qa", "role": "QA"}, release_id=delivery[1])
    assert result["release"]["entries"]
    assert result["qa_report"][section] is None
    assert result["qa_report"]["errors"] == {section: "上游读取失败"}
    other = "delivery_images" if section == "failure_summary" else "failure_summary"
    assert result["qa_report"][other] is not None


def test_empty_report_does_not_read_sections_without_a_release(temp_db, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("no release selected")
    monkeypatch.setattr(qa_service, "delivery_images_view", unexpected)
    monkeypatch.setattr(qa_results_service, "failure_summary", unexpected)
    result = qa_report_service.get_report(temp_db, user={"username": "qa", "role": "QA"})
    assert result["release"] is None
    assert result["qa_report"] == {"failure_summary": None, "delivery_images": None, "errors": {}}


@pytest.mark.parametrize("role,status", [("QA", 200), ("RM", 200), ("Owner", 200), ("Guest", 200), ("SPD", 403), ("Admin", 403)])
def test_report_api_is_read_only_and_scoped_like_the_qa_page(temp_db, delivery, prepared, role, status):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/qa/report", params={"release_id": delivery[1]})
        assert response.status_code == status, response.text
        if status == 200:
            assert response.headers["cache-control"] == "no-store"
            result = response.json()
            assert result["release"]["id"] == delivery[1]
            assert result["qa_report"]["failure_summary"]["failed_rows"] == 1
            assert delivery[0] in result["qa_report"]["delivery_images"]["entries"]
            default = client.get("/api/qa/report").json()
            assert default["release"]["id"] == delivery[1]
    assert entries_repo.get_entry(temp_db, prepared["id"]) == prepared
