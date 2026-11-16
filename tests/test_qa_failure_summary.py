"""QA log reads stored report failures; each App keeps a manual QA comment."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain import qa_results
from app.integrations import harbor
from app.main import create_app
from app.repositories import entries_repo, qa_results_repo
from app.services import cycle_service, lookup_service, qa_results_service, qa_service
from tests.conftest import close_qa, seed_app, seed_cycle, seed_signup


def row(app="AlphaFold3 CP", **changes):
    return qa_results.canonical_result({
        "app_id": {"AlphaFold3 CP": "fold", "AlphaGenome": "genome"}.get(app, app), "app_name": app, "git_branch": "maca", "hpc_version": "3.10.0.7", "app_version": "v3.0.1",
        "arch": "x86", "OS": "ubuntu22.04", "GPU": "C600", "py_label": "py312", "torch_label": "torch2.8",
        "test_name": "af3_cp1x1_unitest", "qa_result": "/", "qa_status": "fail", "qa_comment": "测试失败",
        **changes,
    })


def save(conn, release_id, rows):
    qa_results_repo.save(conn, release_id=release_id, filename="report.xlsx", content=b"saved workbook",
                         environments=[], uploaded_at="2026-11-16 10:00:00", uploaded_by="qa",
                         rows=[(qa_results.worksheet_result_key(data), data, "") for data in rows])
    conn.commit()


@pytest.fixture()
def reports(temp_db):
    seed_app(temp_db, "fold", official_name="AlphaFold3 CP")
    seed_app(temp_db, "genome", official_name="AlphaGenome")
    release = seed_cycle(temp_db, name="3.10.0")
    seed_signup(temp_db, release, "fold")
    seed_signup(temp_db, release, "genome", target_version="v0.10.0")
    save(temp_db, release, [row(), row(test_name="af3_cp2x2_unitest"),
        row(test_name="passed", qa_status="pass", qa_result="fail"),
        row("AlphaGenome", app_version="v0.9.0", GPU="C500", test_name="alphagenome_simple_test", qa_result=15, qa_comment="性能回退"),
        row("AlphaGenome", app_version="v0.9.0", test_name="alphagenome_simple_test"),
        row(test_name="legacy-fail", qa_status="", qa_result="FAIL", qa_comment="legacy"),
        row(test_name="unknown", qa_status="", qa_result=0, qa_comment="性能回退"),
        row(test_name="legacy-pass", qa_status="", qa_result="pass"),
    ])
    return release


def test_summary_preserves_gpu_test_versions_and_comments_without_network_or_verdict_writes(temp_db, reports, monkeypatch):
    monkeypatch.setattr(harbor, "get_artifact", lambda *_: pytest.fail("failure summaries never query Harbor"))
    monkeypatch.setattr(lookup_service, "resolve_images", lambda *_: pytest.fail("failure summaries use saved results"))
    before = temp_db.total_changes
    summary = qa_results_service.failure_summary(temp_db, reports)
    assert summary["total_rows"] == 8 and summary["failed_rows"] == 5
    groups = {group["app_id"]: group for group in summary["groups"]}
    assert {data["test_name"] for data in groups["fold"]["failures"]} == {"af3_cp1x1_unitest", "af3_cp2x2_unitest", "legacy-fail"}
    assert {(data["GPU"], data["qa_comment"]) for data in groups["genome"]["failures"]} == {("C500", "性能回退"), ("C600", "测试失败")}
    assert all(data["app_version"] == "v0.9.0" and data["hpc_version"] == "3.10.0.7" for data in groups["genome"]["failures"])
    assert entries_repo.find_entry(temp_db, reports, "fold")["qa_status"] == "not_checked"
    assert temp_db.total_changes == before


def test_summary_is_cycle_scoped_and_refreshes_after_report_replacement(temp_db, reports):
    other = seed_cycle(temp_db, name="3.9.0")
    save(temp_db, other, [row(hpc_version="3.9.0.12", test_name="other-cycle")])
    assert qa_results_service.failure_summary(temp_db, other)["failed_rows"] == 1
    assert qa_results_service.failure_summary(temp_db, reports)["failed_rows"] == 5
    temp_db.execute("DELETE FROM qa_test_reports WHERE release_id=?", (reports,))
    temp_db.commit()
    save(temp_db, reports, [row(test_name="new-report", qa_status="pass")])
    summary = qa_results_service.failure_summary(temp_db, reports)
    assert summary["total_rows"] == 1 and summary["failed_rows"] == 0 and summary["groups"] == []


def test_legacy_report_families_and_unmatched_branches_stay_explicit(temp_db, reports):
    save(temp_db, None, [row(test_name="legacy-branch", git_branch="old-branch"),
                          row(test_name="other-family", hpc_version="3.9.0.12")])
    summary = qa_results_service.failure_summary(temp_db, reports)
    assert summary["failed_rows"] == 6
    unassigned = next(group for group in summary["groups"] if group["git_branch"] == "old-branch")
    assert unassigned["app_id"] is None and unassigned["failures"][0]["test_name"] == "legacy-branch"


@pytest.mark.parametrize("role,status", [("QA", 200), ("RM", 200), ("Owner", 200), ("Guest", 200), ("SPD", 403), ("Admin", 403)])
def test_failure_api_permissions_and_response(temp_db, reports, role, status):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/qa/failure-summary", params={"release_id": reports})
        assert response.status_code == status
        if status == 200:
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["failed_rows"] == 5
            assert client.get("/api/qa/failure-summary", params={"release_id": "missing"}).status_code == 404


@pytest.mark.parametrize("status", ["not_checked", "qa_passed", "has_issues", "cannot_release"])
def test_each_app_comment_is_saved_for_every_verdict_without_revalidating_unchanged_images(temp_db, reports, monkeypatch, status):
    entry = entries_repo.find_entry(temp_db, reports, "fold")
    revision = cycle_service.entry_view(temp_db, entry)["delivery_revision"]
    monkeypatch.setattr(harbor, "get_artifact", lambda *_: pytest.fail("a comment edit does not change images"))
    qa_service.set_qa_status_batch(temp_db, reports, [{"app_id": "fold", "status": status,
        "issue_note": "QA 手填 comment\n第二行", "delivery_revision": revision}], user="qa", role="QA")
    saved = entries_repo.find_entry(temp_db, reports, "fold")
    assert saved["qa_status"] == status and saved["qa_note"] == "QA 手填 comment\n第二行"
    assert entries_repo.find_entry(temp_db, reports, "genome")["qa_note"] == ""
    assert qa_results_service.failure_summary(temp_db, reports)["failed_rows"] == 5


def test_comment_conflicts_and_published_cycles_reject_changes_without_losing_the_saved_comment(temp_db, reports):
    entry = entries_repo.find_entry(temp_db, reports, "fold")
    revision = cycle_service.entry_view(temp_db, entry)["delivery_revision"]
    item = {"app_id": "fold", "status": "qa_passed", "issue_note": "first comment", "delivery_revision": revision}
    qa_service.set_qa_status_batch(temp_db, reports, [item], user="qa", role="QA")
    with pytest.raises(ValueError, match="已变化"):
        qa_service.set_qa_status_batch(temp_db, reports, [{**item, "issue_note": "stale comment"}], user="qa", role="QA")
    close_qa(temp_db, reports)
    cycle_service.publish(temp_db, reports, user="rm", role="RM")
    with pytest.raises(RuntimeError, match="版本已发布"):
        qa_service.set_qa_status_batch(temp_db, reports, [{**item, "issue_note": "locked comment"}], user="qa", role="QA")
    assert entries_repo.find_entry(temp_db, reports, "fold")["qa_note"] == "first comment"
