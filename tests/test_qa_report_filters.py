"""QA log filtering, sorting and export cover records beyond the first page."""
from __future__ import annotations

import csv
import io
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain import qa_results
from app.main import create_app
from app.repositories import qa_results_repo
from app.services import lookup_service, qa_results_service
from tests.test_image_lookup import IMAGE
from tests.test_qa_results import live as live, report_release


@pytest.fixture()
def reports(temp_db, live):
    raw, calls = live
    config = deepcopy(raw["app_test"]["sanity"])
    config["supported_chip"] = {"C500": ["ubuntu22.04_amd64"]}
    raw["app_test"] = {f"case{index}": {**deepcopy(config), "test_cmd": f"run-case-{index}"}
                       for index in range(65)}
    source = lookup_service.resolve_images(temp_db, [IMAGE])["images"][0]
    rows = qa_results.worksheet_commands(source, hpc_version="3.10.1.2")
    rows.sort(key=lambda row: qa_results.report_sort_key(row, "test_name"))
    for index, row in enumerate(rows):
        row.update(qa_digest=IMAGE, qa_docker_cmd=f"QA command {index}", qa_env="C500_server_1",
                   qa_status="pass" if index < 60 else "fail", qa_result=0,
                   qa_comment='尾部, "备注"\n第二行' if index >= 60 else "")
    qa_results_repo.save(temp_db, filename="分页报告.xlsx", content=b"workbook", environments=["C500_server_1"],
                         uploaded_at="2026-11-15 12:30:00", uploaded_by="qa",
                         rows=[(qa_results.worksheet_result_key(row), row, IMAGE) for row in rows])
    temp_db.commit()
    calls.clear()
    return raw, calls


def test_column_filters_search_all_records_before_pagination(temp_db, reports):
    page = qa_results_service.query(temp_db)
    assert page["total"] == 65 and len(page["rows"]) == 50
    assert not any(row["qa_status"] == "fail" for row in page["rows"])
    filtered = qa_results_service.query(temp_db, column_filters={"qa_status": "FAIL", "GPU": "C500"})
    assert filtered["total"] == 5
    assert [row["test_name"] for row in filtered["rows"]] == [f"case{i}" for i in range(60, 65)]
    assert all(row["website_docker_cmd"] and not row["command_error"] for row in filtered["rows"])
    assert qa_results_service.query(temp_db, column_filters={"qa_result": "0"})["total"] == 65
    assert qa_results_service.query(temp_db, column_filters={"原始 Excel": "分页", "提交人": "qa"})["total"] == 65
    assert qa_results_service.query(temp_db, column_filters={"qa_status": "fail", "test_name": "case1"})["total"] == 0


def test_global_search_and_live_command_filters_combine_and_never_use_saved_command(temp_db, reports):
    raw, _ = reports
    # Live app_info changes while the stored upload command remains untouched.
    raw["app_test"]["case64"]["test_cmd"] = "new-live-command"
    result = qa_results_service.query(temp_db, table_filter="new-live-command", search="testapp",
                                     column_filters={"qa_status": "fail"})
    assert result["total"] == 1 and result["rows"][0]["test_name"] == "case64"
    assert qa_results_service.query(temp_db, column_filters={"website_docker_cmd": "new-live-command"})["total"] == 1
    assert qa_results_service.query(temp_db, table_filter="new-live-command", search="missing")["total"] == 0
    assert qa_results_service.query(temp_db, table_filter="worksheet_result_key")["total"] == 0
    assert qa_results_service.query(temp_db, column_filters={"website_docker_cmd": "run-case-64"})["total"] == 0


def test_sorting_and_export_include_all_filtered_records(temp_db, reports):
    page = qa_results_service.query(temp_db, sort_by="test_name", sort_direction="desc", offset=50, limit=10)
    assert page["total"] == 65
    assert [row["test_name"] for row in page["rows"]] == [f"case{i}" for i in range(14, 4, -1)]
    content = qa_results_service.export_csv(temp_db, column_filters={"GPU": "C500"},
                                           sort_by="test_name", sort_direction="desc")
    assert content.startswith("\ufeff")
    exported = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    assert len(exported) == 65 and list(exported[0]) == qa_results.REPORT_COLUMNS
    assert exported[0]["test_name"] == "case64" and exported[-1]["test_name"] == "case0"
    assert exported[0]["qa_comment"] == '尾部, "备注"\n第二行'
    assert exported[0]["qa_result"] == "0" and exported[0]["原始 Excel"] == "分页报告.xlsx"
    assert "@sha256:" in exported[0]["website_docker_cmd"]


def test_metadata_filters_do_not_resolve_images_outside_the_page(temp_db, reports):
    _, calls = reports
    result = qa_results_service.query(temp_db, column_filters={"qa_status": "fail"}, offset=20)
    assert result["total"] == 5 and result["rows"] == []
    assert calls == []


def test_http_filters_export_and_invalid_parameters(temp_db, reports):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    params = {"release_id": report_release(temp_db), "column_filters": json.dumps({"qa_status": "fail"}), "sort_by": "test_name", "sort_direction": "desc"}
    with TestClient(app) as client:
        response = client.get("/api/qa/test-results", params={**params, "offset": 0, "limit": 2})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 5
        assert [row["test_name"] for row in response.json()["rows"]] == ["case64", "case63"]
        exported = client.get("/api/qa/test-results/export", params=params)
        assert exported.status_code == 200 and exported.headers["cache-control"] == "no-store"
        assert len(list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))) == 5
        assert "attachment" in exported.headers["content-disposition"]
        for invalid in ({"column_filters": "bad-json"}, {"column_filters": "[]"},
                        {"column_filters": '{"qa_status":123}'}, {"column_filters": '{"unknown":"x"}'},
                        {"sort_by": "unknown"}):
            for path in ("/api/qa/test-results", "/api/qa/test-results/export"):
                assert client.get(path, params={"release_id": params["release_id"], **invalid}).status_code == 400
        assert client.get("/api/qa/test-results", params={"sort_direction": "invalid"}).status_code == 422
