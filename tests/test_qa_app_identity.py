"""App IDs distinguish same-name Apps in commands, reports and old workbooks."""
from copy import deepcopy

import pytest

from app.domain import qa_results
from app.repositories import qa_results_repo
from app.services import catalog_service, lookup_service, qa_result_identity_service, qa_results_service
from tests.conftest import seed_app, seed_cycle, seed_signup
from tests.test_image_lookup import IMAGE
from tests.test_qa_results import live as live, template, upload


def test_digest_selects_pinned_app_id_among_same_name_manifests(temp_db, live, monkeypatch):
    raw = deepcopy(live[0])
    raw["app_version"] = "old-version"
    seed_app(temp_db, "older-app", official_name="TestApp", git_branch="maca",
             git_url="APP/example/old.xml", app_info=raw, commit_id="1234567")
    monkeypatch.setattr(catalog_service, "app_info_fetch_target", lambda app: ("hpc_actual_repo", "maca"))
    row = template(temp_db)
    assert row["app_id"] == "testapp" and "app_name" not in row
    upload(temp_db, [row])
    with pytest.raises(ValueError, match="app_id 不匹配"):
        upload(temp_db, [{**row, "app_id": "older-app"}])
    assert qa_results_service.query(temp_db)["rows"][0]["app_id"] == "testapp"


def test_same_name_and_branch_failures_link_by_id_with_both_apps_signed_up(temp_db):
    release = seed_cycle(temp_db, name="3.10.0")
    rows = []
    for app_id in ("lammps-old", "lammps-new"):
        seed_app(temp_db, app_id, official_name="LAMMPS", git_branch="master")
        seed_signup(temp_db, release, app_id)
        rows.append(qa_results.canonical_result({"app_id": app_id, "git_branch": "master", "hpc_version": "3.10.0.7",
                    "app_version": "1", "test_name": "same-test", "GPU": "C500", "qa_status": "fail",
                    "qa_comment": app_id}))
    qa_results_repo.save(temp_db, release_id=release, filename="same-names.xlsx", content=b"workbook",
                         environments=[], uploaded_at="now", uploaded_by="qa",
                         rows=[(qa_results.worksheet_result_key(row), row, "") for row in rows])
    temp_db.commit()
    groups = qa_results_service.failure_summary(temp_db, release)["groups"]
    assert len(groups) == 2
    assert {group["app_id"]: group["failures"][0]["qa_comment"] for group in groups} == {
        "lammps-old": "lammps-old", "lammps-new": "lammps-new"}


@pytest.mark.parametrize("ambiguous", [False, True])
def test_old_report_projection_never_rewrites_or_guesses(temp_db, live, ambiguous):
    row = template(temp_db)
    row.pop("app_id")
    row.update(app_name="obsolete display name", qa_digest_gerrit_commit_id="bb46c60", qa_result="fail")
    qa_results_repo.save(temp_db, filename="legacy.xlsx", content=b"original", environments=[],
                         uploaded_at="before", uploaded_by="qa", rows=[("original-key", row, IMAGE)])
    temp_db.commit()
    if ambiguous:
        seed_app(temp_db, "other-app", app_info=live[0], commit_id="bb46c60")
    before = temp_db.total_changes
    stored = temp_db.execute("SELECT result_key,data_json FROM qa_test_results").fetchone()[:]
    data = qa_result_identity_service.list_records(temp_db)[0]["data"]
    assert data["app_id"] == ("" if ambiguous else "testapp")
    assert bool(data.get("app_id_error")) == ambiguous
    assert temp_db.total_changes == before
    assert temp_db.execute("SELECT result_key,data_json FROM qa_test_results").fetchone()[:] == stored
    if not ambiguous:
        assert lookup_service.batch_images(temp_db, [IMAGE])["rows"][0]["test_result"] == "fail"


def test_ids_are_case_sensitive_instead_of_casefolding_display_names():
    row = {"app_id": "Example", "git_branch": "maca"}
    assert qa_results.result_key(row) != qa_results.result_key({**row, "app_id": "example"})
